"""
Accountability Layer — Structural Contract
Defines what any agent's output must look like, regardless of provider.

The accountability layer is LLM-agnostic. This module only knows about
the structural contract: two XML blocks, nothing outside them.
Any agent — OpenClaw, Gemini, GPT, local model — must satisfy this contract.

ADR-01b: Directive injection is the mechanism that enforces this contract upstream.
ADR-07:  StructuralParseError triggers the validation loop in pipeline/middleware.py.
SEC-01:  thought_log is extracted here; middleware decides which tier sees it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

# re.MULTILINE makes ^ match at the start of each line.
# This prevents false matches on inline backtick references like `<thought_log>`
# or pre-amble text that mentions the tag names mid-sentence.
_THOUGHT_LOG_RE = re.compile(r"^\s*<thought_log>(.*?)</thought_log>", re.DOTALL | re.MULTILINE)
_CONCLUSION_RE  = re.compile(r"^\s*<conclusion>(.*?)</conclusion>",   re.DOTALL | re.MULTILINE)
# B4: the optional third block, matched only directly after </conclusion> (whitespace between). An unclosed
# one runs to the end of the response (group 2 empty), so it is recorded as unclosed
# rather than failing the response.
_ASSESSMENT_RE  = re.compile(r"\s*<assessment>(.*?)(</assessment>|\Z)", re.DOTALL)


class StructuralParseError(Exception):
    """
    Raised when an agent's response fails structural validation.
    Carries the raw response so the middleware can log it as PARSE_FAILURE.
    """

    def __init__(self, message: str, raw_response: str) -> None:
        super().__init__(message)
        self.raw_response = raw_response


@dataclass(frozen=True)
class AgentResponse:
    """
    A structurally valid agent response.
    Provider-agnostic — produced from any raw text that satisfies the contract.
    """
    thought_log: str   # content of <thought_log> block
    conclusion: str    # content of <conclusion> block
    raw_text: str      # full original response, preserved for audit
    # B4 (directive v1.6.0+ only): the <assessment> block's inner text, and whether
    # its closing tag was found. Both None when the parser wasn't asked to look;
    # text None + closed False when it looked and found no block.
    assessment_text: str | None = None
    assessment_closed: bool | None = None


def _parse_response(raw: str, *, allow_assessment: bool = False) -> AgentResponse:
    """
    Parse raw agent output into AgentResponse.

    Raises StructuralParseError if:
    - Either block is missing
    - Both blocks are missing
    - Any text exists outside the two blocks

    allow_assessment (B4): for a directive that asks for it (core.assessment.
    expects_assessment), an <assessment> block after </conclusion> is also accepted,
    closed or not, and returned unvalidated. It is never required: its absence is
    not a structural failure. With allow_assessment False — every directive up to
    v1.5.2, and the corrective retry — the two-block contract is byte-for-byte what
    it was, so an <assessment> block there is "text outside XML blocks".

    Note: <conclusion> is searched only in the text AFTER </thought_log> to
    avoid false matches when the model describes the format using backtick-wrapped
    tag names (e.g. `<conclusion>`) inside the thought_log content.
    """
    thought_match = _THOUGHT_LOG_RE.search(raw)

    # Search for <conclusion> starting from the end of <thought_log> block.
    # Falls back to full-string search if thought_log is missing (so we can
    # still report the correct missing-block error below).
    search_from = thought_match.end() if thought_match else 0
    conclusion_match = _CONCLUSION_RE.search(raw, search_from)

    if not thought_match and not conclusion_match:
        raise StructuralParseError(
            "Response missing both <thought_log> and <conclusion> blocks", raw
        )
    if not thought_match:
        raise StructuralParseError("Response missing <thought_log> block", raw)
    if not conclusion_match:
        raise StructuralParseError("Response missing <conclusion> block", raw)

    assessment_match = _ASSESSMENT_RE.match(raw, conclusion_match.end()) if allow_assessment else None

    # Remainder check: remove every matched span (position-aware) so we don't
    # accidentally re-match the fake tags inside the thought_log content.
    spans = sorted([thought_match.span(), conclusion_match.span()]
                   + ([assessment_match.span()] if assessment_match else []))
    remainder, at = "", 0
    for start, end in spans:
        remainder += raw[at:start]
        at = end
    remainder += raw[at:]
    if remainder.strip():
        raise StructuralParseError(
            "Response contains text outside XML blocks", raw
        )

    return AgentResponse(
        thought_log=thought_match.group(1).strip(),
        conclusion=conclusion_match.group(1).strip(),
        raw_text=raw,
        assessment_text=assessment_match.group(1) if assessment_match else None,
        assessment_closed=(bool(assessment_match.group(2)) if assessment_match else False) if allow_assessment else None,
    )


# ── Content checks beyond block structure ──────────────────────────────────────
# Well-formed blocks are necessary, not sufficient. Measured on 2026-09-24: 7 of 10
# stored v1.5.0 cross-agent conclusions opened with the directive's own
# instruction text, and all of them passed the structural parse and were delivered
# as answers. A conclusion that is the directive restated is no more an answer than
# a missing <conclusion> block, so it fails the same way — StructuralParseError,
# which sends it through ADR-07's retry and records the attempt as PARSE_FAILURE.

_WORD_RE = re.compile(r"[a-z0-9]+")
# An echo is any run of this many consecutive directive words copied verbatim.
# Chosen by measurement, not intuition: replaying every stored conclusion (135)
# against the directive version that produced it, windows of 6, 8, 10 and 12 words
# all flagged exactly the same 17 conclusions — every real echo, including partial
# copies — and none of the 118 clean ones. 8 sits inside that plateau with margin.
_ECHO_WINDOW = 8


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _windows(words: list[str]) -> list[str]:
    return [" ".join(words[i:i + _ECHO_WINDOW]) for i in range(len(words) - _ECHO_WINDOW + 1)]


# Sentences the directive *tells* the model to say, in substance, when the Context
# is thin. A compliant answer may reuse their wording ("the Context does not
# contain what is needed to answer ..."), so a window drawn only from them is not
# an echo — flagging it would penalize exactly the behavior the directive asks for.
_PERMITTED_ANSWER_WORDING = (
    "If the Context is empty or does not contain what is needed to answer, say exactly that",
    "an honest insufficient context is correct behavior, not a failure",
    "If the Context did not support a claim, say so instead of asserting it",
)


@lru_cache(maxsize=32)
def _directive_windows(directive_text: str) -> frozenset[str]:
    permitted = {w for phrase in _PERMITTED_ANSWER_WORDING for w in _windows(_words(phrase))}
    return frozenset(_windows(_words(directive_text))) - permitted


def find_directive_echo(conclusion: str, directive_text: str) -> str | None:
    """
    The first _ECHO_WINDOW-word run of `conclusion` that also appears in the
    directive (lowercased, punctuation dropped), or None. Deterministic — no fuzzy
    matching — so a hit is always a verbatim copy that can be shown to a reviewer.
    """
    windows = _directive_windows(directive_text)
    return next((w for w in _windows(_words(conclusion)) if w in windows), None)


def reject_unusable_conclusion(response: AgentResponse, directive_text: str) -> None:
    """
    Raise StructuralParseError if the conclusion is empty or restates the directive.

    Called by pipeline/middleware.py on every attempt (the authoritative check) and
    by web/step_trace.py's adapter wrapper (so the trace step reads parse_failure
    rather than ok). Only the conclusion is checked: a thought_log that quotes a rule
    it is applying ("per the GROUNDING RULE ...") is reasoning, not an echo.
    """
    if not response.conclusion.strip():
        raise StructuralParseError("Response has an empty <conclusion> block", response.raw_text)
    echoed = find_directive_echo(response.conclusion, directive_text)
    if echoed is not None:
        raise StructuralParseError(
            f"Conclusion repeats the directive's own instructions: {echoed!r}",
            response.raw_text,
        )
