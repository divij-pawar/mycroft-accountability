"""
Accountability Layer — Phase 2: Validation Loop
Implements ADR-07: single retry on structural parse failure.

Both attempts are always written to the audit record.
Rising parse_failure_rate across runs is a drift signal for directive decay.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from core.assessment import (
    EXTRACTION_PROMPT_VERSION,
    ParsedAssessment,
    expects_assessment,
    extract_assessment,
    parse_assessment,
)
from core.contracts import AgentAdapter, ModelCall
from core.parsing import AgentResponse, StructuralParseError, reject_unusable_conclusion
from core.directive import DirectiveVersion, get_active_directive
from core.schemas import AgentID, DataSource, ParseStatus, ReasoningObject

# Injected as the directive on the second attempt (ADR-07)
CORRECTIVE_DIRECTIVE_TEXT = (
    "Your previous response failed structural validation. "
    "You must provide your internal reasoning in a <thought_log> block "
    "and your final output in a <conclusion> block. "
    "Adhere to the schema exactly."
)

_CORRECTIVE_DIRECTIVE = DirectiveVersion(
    version="corrective", text=CORRECTIVE_DIRECTIVE_TEXT
)


class HaltError(Exception):
    """
    Raised when the validation loop exhausts both attempts without a successful parse.
    Carries all ReasoningObjects written so the caller can persist the audit trail
    before propagating the halt upstream.
    """

    def __init__(self, message: str, reasoning_objects: list[ReasoningObject]) -> None:
        super().__init__(message)
        self.reasoning_objects = reasoning_objects


@dataclass
class ValidationLoopResult:
    reasoning_objects: list[ReasoningObject]
    final_response: AgentResponse | None  # None when halted


def _build_reasoning_object(
    *,
    run_id: uuid.UUID,
    agent_id: AgentID,
    attempt_number: int,
    parse_status: ParseStatus,
    confidence_score: float,
    directive: DirectiveVersion,
    context_window: dict[str, str],
    thought_log: str | None = None,
    conclusion: str | None = None,
    raw_text: str | None = None,
    data_sources: tuple[DataSource, ...] = (),
    response: AgentResponse | None = None,
    extracted: ParsedAssessment | None = None,
) -> ReasoningObject:
    raw_output = {"text": raw_text} if raw_text is not None else None
    # B4: a successful attempt under a directive that asks for an <assessment> gets
    # it validated and recorded — absent, unclosed and invalid included, each with
    # its reason. Never a failure: the attempt's status is already decided.
    assessment, status, issues, source = None, None, (), None
    if response is not None and expects_assessment(directive.version):
        parsed = parse_assessment(response.assessment_text,
                                  closed=bool(response.assessment_closed) if response.assessment_text is not None else None)
        assessment, status, issues, source = parsed.assessment, parsed.status, tuple(parsed.issues), "directive"
    elif extracted is not None:
        # Option 1: a separate call read the finished answer. Its reply is kept verbatim.
        assessment, status, issues = extracted.assessment, extracted.status, tuple(extracted.issues)
        source = EXTRACTION_PROMPT_VERSION
        raw_output = {**(raw_output or {}), "assessment_extraction": {
            "prompt_version": EXTRACTION_PROMPT_VERSION, "reply": extracted.raw}}
    return ReasoningObject(
        run_id=run_id,
        agent_id=agent_id,
        attempt_number=attempt_number,
        parse_status=parse_status,
        confidence_score=confidence_score,
        thought_log=thought_log,
        conclusion=conclusion,
        raw_output=raw_output,
        data_sources=data_sources,
        directive_version=directive.version,
        directive_text=directive.text,
        context_window=context_window,
        assessment=assessment,
        assessment_status=status,
        assessment_issues=issues,
        assessment_source=source,
    )


def _extract(assess_fn: ModelCall | None, directive: DirectiveVersion, subject: str, context: str,
             response: AgentResponse) -> ParsedAssessment | None:
    """Option 1's extraction for a successful answer, unless the directive asks for the block itself."""
    if assess_fn is None or expects_assessment(directive.version):
        return None
    return extract_assessment(assess_fn, subject, response.conclusion, response.thought_log, context)


def run_validation_loop(
    ticker: str,
    context: str,
    run_id: uuid.UUID,
    agent_id: AgentID,
    *,
    confidence_score: float = 0.7,
    data_sources: tuple[DataSource, ...] = (),
    directive: DirectiveVersion | None = None,
    call_agent_fn: AgentAdapter | None = None,
    assess_fn: ModelCall | None = None,
) -> ValidationLoopResult:
    """
    ADR-07 validation loop.

    call_agent_fn must be provided — the accountability layer has no default LLM.
    Signature: (subject: str, context: str, directive: DirectiveVersion) -> AgentResponse

    Attempt 1 — active directive:
      SUCCESS  → one ReasoningObject (attempt=1, SUCCESS). Done.
      FAILURE  → log attempt=1 PARSE_FAILURE, proceed to attempt 2.

    Attempt 2 — corrective directive:
      SUCCESS  → two ReasoningObjects (attempt=1 PARSE_FAILURE, attempt=2 SUCCESS). Done.
      FAILURE  → two ReasoningObjects (attempt=1 PARSE_FAILURE, attempt=2 HALT).
                 Raises HaltError carrying those objects. No grade delivered.

    assess_fn (B4, option 1): if given, the successful attempt's answer is read by
    one more model call for its grade/direction (core.assessment.extract_assessment),
    after the structural check has passed — so it can't affect retry or halt, and a
    failed extraction is recorded, never raised. Not used when the directive already
    asks for an in-answer <assessment> block (v1.6.0+).
    """
    if call_agent_fn is None:
        raise TypeError(
            "call_agent_fn is required. "
            "Provide an adapter for your agent: "
            "f(subject, context, directive) -> AgentResponse"
        )
    if directive is None:
        directive = get_active_directive()

    # Same for every attempt — only the directive differs between attempt 1
    # and the corrective retry. Recorded per-attempt on the ReasoningObject
    # anyway (not hoisted out), so each attempt's record is self-describing
    # without the reader having to cross-reference this call.
    context_window = {"subject": ticker, "context": context}

    objects: list[ReasoningObject] = []

    # ── Attempt 1 ──────────────────────────────────────────────────────────
    # "Structural" includes an empty conclusion or one that restates the directive
    # (core/parsing.py's reject_unusable_conclusion) — both parse as XML but neither
    # is an answer, so both take the same retry-then-halt path as a missing block.
    try:
        response = call_agent_fn(ticker, context, directive)
        reject_unusable_conclusion(response, directive.text)
    except StructuralParseError as exc:
        objects.append(
            _build_reasoning_object(
                run_id=run_id,
                agent_id=agent_id,
                attempt_number=1,
                parse_status=ParseStatus.PARSE_FAILURE,
                confidence_score=confidence_score,
                directive=directive,
                context_window=context_window,
                raw_text=exc.raw_response,
                data_sources=data_sources,
            )
        )

        # ── Attempt 2 (corrective) ──────────────────────────────────────────
        try:
            response = call_agent_fn(ticker, context, _CORRECTIVE_DIRECTIVE)
            reject_unusable_conclusion(response, _CORRECTIVE_DIRECTIVE.text)
        except StructuralParseError as exc2:
            objects.append(
                _build_reasoning_object(
                    run_id=run_id,
                    agent_id=agent_id,
                    attempt_number=2,
                    parse_status=ParseStatus.HALT,
                    confidence_score=confidence_score,
                    directive=_CORRECTIVE_DIRECTIVE,
                    context_window=context_window,
                    raw_text=exc2.raw_response,
                    data_sources=data_sources,
                )
            )
            raise HaltError(
                f"Agent {agent_id.value!r} failed structural validation twice for {ticker!r}. "
                "Pipeline halted — no grade delivered.",
                objects,
            ) from exc2

        objects.append(
            _build_reasoning_object(
                run_id=run_id,
                agent_id=agent_id,
                attempt_number=2,
                parse_status=ParseStatus.SUCCESS,
                confidence_score=confidence_score,
                directive=_CORRECTIVE_DIRECTIVE,
                context_window=context_window,
                thought_log=response.thought_log,
                conclusion=response.conclusion,
                raw_text=response.raw_text,
                data_sources=data_sources,
                response=response,
                extracted=_extract(assess_fn, _CORRECTIVE_DIRECTIVE, ticker, context, response),
            )
        )
        return ValidationLoopResult(reasoning_objects=objects, final_response=response)

    # Attempt 1 succeeded
    objects.append(
        _build_reasoning_object(
            run_id=run_id,
            agent_id=agent_id,
            attempt_number=1,
            parse_status=ParseStatus.SUCCESS,
            confidence_score=confidence_score,
            directive=directive,
            context_window=context_window,
            thought_log=response.thought_log,
            conclusion=response.conclusion,
            raw_text=response.raw_text,
            data_sources=data_sources,
            response=response,
            extracted=_extract(assess_fn, directive, ticker, context, response),
        )
    )
    return ValidationLoopResult(reasoning_objects=objects, final_response=response)
