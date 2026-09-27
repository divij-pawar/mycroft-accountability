"""
Cross-Agent Validation — compare two agents' conclusions about the same subject.

Implements the design in the Cross-Agent Validation SDD v1.

What it does
    Runs two agents independently, each through the unmodified ADR-07 validation
    loop (middleware.run_validation_loop), then compares their final conclusions
    for numeric divergence. Both agents' full attempt history is returned so the
    caller can persist it as evidence.

What it does NOT do
    It does not decide which agent is right. A flagged contradiction means the two
    conclusions cite different numbers, nothing more. Resolution is a human's job.

Relationship to validation/consistency.py
    validation/consistency.py compares one agent against a *repeat of itself* and deliberately
    throws the probe run away (fresh UUID, never persisted) — it is a private sanity
    check on a single run. This module is the opposite on purpose: two *different*
    agents, one shared run_id, and both agents' records persisted, because here the
    comparison itself is the evidence. The scoring primitives are imported from
    validation/consistency.py unmodified rather than reimplemented.

Scope limit worth knowing (SDD §14)
    Comparison is numeric only. Two conclusions that disagree in substance but cite
    the same figures will not be flagged. This detects number mismatches, not
    reasoning mismatches.

Known gap (deliberate, per SDD §9)
    Only HaltError is caught per agent. If an agent's adapter raises something else
    (a rate-limit error, an EDGAR fetch failure), it propagates and the whole
    comparison aborts — including the other agent's already-collected records.
    ComparisonStatus has no ERROR state in v1; adding one is a design decision, not
    something to improvise here. Under concurrent execution the other agent still
    runs to completion before the exception propagates (the thread pool waits for
    it), but its records are discarded the same way.

Concurrency
    The two agents are independent by construction — neither sees the other's
    context or output — so by default they run on two threads at once
    (`concurrent=True`). CROSS_AGENT_MAX_CONCURRENCY=1 in the environment forces
    the old A-then-B order, e.g. for a local Ollama that can only hold one model in
    memory. Result ordering is unaffected either way: reasoning_objects is always
    agent A's attempts then agent B's, whichever thread finished first.
"""

from __future__ import annotations

import contextvars
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Literal

from validation.concept_linkage import contradiction_flag_concept_aware
from validation.facts import contradiction_flag_canonical
from validation.constraints import run_checks
from validation.divergence import synthesize
from validation.consistency import (
    ConsistencyAgreement,
    _classify,
    _compute_score,
    _extract_numbers,
)
from pipeline.middleware import HaltError, run_validation_loop
from core.schemas import AgentID, DataSource, ParseStatus, ReasoningObject

# "symmetric_difference" (default) preserves every existing test and caller's
# behavior unchanged — see run_cross_agent_validation's docstring for exactly
# what it does. "concept_aware" delegates to validation/concept_linkage.py,
# which measures materially better on the real-run corpus (kills 15 of 16
# disjoint-concept false positives, preserves the one confirmed true positive —
# see tests/test_concept_linkage.py) but is currently tuned to Producer A/B's
# specific concept vocabularies (FINANCIAL_LENS / EARNINGS_LENS), not a general
# solution for an arbitrary third producer pair.
# "canonical_facts" (2026-09-24, validation/facts.py) compares figures by metric and
# period and recomputes one-sided derived ratios. On the labeled corpus it flags 6
# of 16 disjoint-concept runs vs concept_aware's 1 — five are ratios stated without
# their components, whose inputs the corpus never recorded, so they can't be judged
# either way. Per the roadmap's rule it is therefore opt-in for the financial pairing,
# and the default for generic same-context comparisons. Its per-figure rows are
# computed for every compared run regardless of which rule decides the flag.
ContradictionRule = Literal["symmetric_difference", "concept_aware", "canonical_facts"]


# ── Status ─────────────────────────────────────────────────────────────────────

class ComparisonStatus(str, Enum):
    """
    Outcome of a cross-agent comparison attempt.

    A halted agent is still evidence: its ReasoningObjects (including the
    HALT-status one) are returned and persisted. A halt means only that there was
    no conclusion on that side to compare, not that the run disappears.
    """
    COMPARED       = "COMPARED"        # both agents produced a conclusion
    AGENT_A_HALTED = "AGENT_A_HALTED"  # producer A failed structural validation twice
    AGENT_B_HALTED = "AGENT_B_HALTED"  # producer B failed structural validation twice
    BOTH_HALTED    = "BOTH_HALTED"


# ── Result ─────────────────────────────────────────────────────────────────────

@dataclass
class CrossAgentComparisonResult:
    """
    One cross-agent comparison.

    Not frozen — follows ConsistencyResult's convention in validation/consistency.py. This is a
    computed value that gets embedded into a payload dict before persistence, not an
    audit record itself. The ReasoningObjects underneath it stay frozen and immutable.

    contradiction_flag is None (not False) when status != COMPARED. False would claim
    "checked, found no contradiction", which is a materially stronger statement than
    "no comparison was possible" — P3 forbids reporting a result that was never produced.
    """
    run_id:             uuid.UUID
    subject:            str
    agent_a_id:         AgentID
    agent_b_id:         AgentID
    status:             ComparisonStatus
    agent_a_conclusion: str | None
    agent_b_conclusion: str | None
    agent_a_numbers:    list[str]
    agent_b_numbers:    list[str]
    divergent_numbers:  list[str]                    # in one conclusion but not both
    contradiction_flag: bool | None                  # None when status != COMPARED
    word_overlap:       float | None
    number_overlap:     float | None
    score:              float | None
    agreement:          ConsistencyAgreement | None
    compared_at:        datetime
    # Figure-by-figure rows from validation/facts.py (None when nothing was compared),
    # and which rule decided contradiction_flag — they can differ, and the reader
    # must be able to see which one the verdict came from.
    metric_comparisons: list[dict[str, Any]] | None = None
    contradiction_rule: str = "symmetric_difference"
    # B3: accounting checks on each agent's own figures, on each figure against the
    # filing value that agent was given, and on the filing itself
    # (validation/constraints.py). None only on results built before B3.
    structural_flags:   dict[str, Any] | None = None
    # B5: divergence class, counted evidence, grade candidates and a consensus only
    # when one exists (validation/divergence.py). None only on results before B5.
    synthesis:          dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Flat, JSON-serialisable dict — becomes one key inside a larger run payload."""
        return {
            "run_id":             str(self.run_id),
            "subject":            self.subject,
            "agent_a_id":         self.agent_a_id.value,
            "agent_b_id":         self.agent_b_id.value,
            "status":             self.status.value,
            "agent_a_conclusion": self.agent_a_conclusion,
            "agent_b_conclusion": self.agent_b_conclusion,
            "agent_a_numbers":    self.agent_a_numbers,
            "agent_b_numbers":    self.agent_b_numbers,
            "divergent_numbers":  self.divergent_numbers,
            "contradiction_flag": self.contradiction_flag,
            "word_overlap":       self.word_overlap,
            "number_overlap":     self.number_overlap,
            "score":              self.score,
            "agreement":          self.agreement,
            "compared_at":        self.compared_at.isoformat(),
            "metric_comparisons": self.metric_comparisons,
            "contradiction_rule": self.contradiction_rule,
            "structural_flags":   self.structural_flags,
            "synthesis":          self.synthesis,
        }


# ── Internals ──────────────────────────────────────────────────────────────────

def _run_one_agent(
    subject:          str,
    context:          str,
    run_id:           uuid.UUID,
    agent_id:         AgentID,
    call_agent_fn:    Callable,
    confidence_score: float,
    data_sources:     tuple[DataSource, ...],
    assess_fn:        Callable | None = None,
) -> tuple[str | None, list[ReasoningObject], bool]:
    """
    Run one agent's ADR-07 loop, preserving its ReasoningObjects even when it halts.

    Returns (conclusion_or_None, reasoning_objects, halted).
    """
    try:
        result = run_validation_loop(
            subject,
            context,
            run_id,
            agent_id,
            confidence_score=confidence_score,
            data_sources=data_sources,
            call_agent_fn=call_agent_fn,
            assess_fn=assess_fn,
        )
    except HaltError as exc:
        # The halt is the evidence — carry its records forward rather than dropping them.
        return None, list(exc.reasoning_objects), True

    objects = list(result.reasoning_objects)
    conclusion = result.final_response.conclusion if result.final_response else None
    # run_validation_loop raises rather than returning final_response=None, so this
    # branch is defensive only. Treat a missing conclusion as "nothing to compare".
    return conclusion, objects, conclusion is None


def _max_concurrency() -> int:
    """CROSS_AGENT_MAX_CONCURRENCY, default 2. An unparseable value keeps the default."""
    try:
        return max(1, int(os.environ.get("CROSS_AGENT_MAX_CONCURRENCY", "2")))
    except ValueError:
        return 2


AgentFinishedCallback = Callable[[str, "str | None", bool], None]


# ── Public API ─────────────────────────────────────────────────────────────────

def run_cross_agent_validation(
    subject:         str,
    context_a:       str,
    context_b:       str,
    agent_a_id:      AgentID,
    agent_b_id:      AgentID,
    call_agent_a_fn: Callable,
    call_agent_b_fn: Callable,
    *,
    run_id:           uuid.UUID | None = None,
    confidence_score: float = 0.7,
    data_sources_a:   tuple[DataSource, ...] = (),
    data_sources_b:   tuple[DataSource, ...] = (),
    concepts_expected_to_overlap: bool = True,
    contradiction_rule: ContradictionRule = "symmetric_difference",
    concurrent: bool = True,
    on_agent_finished: AgentFinishedCallback | None = None,
    include_years: bool = False,
    shared_concepts: frozenset[str] = frozenset(),
    given_facts: dict[str, list[Any]] | None = None,
    source_payload: dict | None = None,
    assess_a_fn: Callable | None = None,
    assess_b_fn: Callable | None = None,
) -> tuple[CrossAgentComparisonResult, list[ReasoningObject]]:
    """
    Run two agents independently on the same subject, then compare their conclusions.

    Each agent gets its own context (that is the point — disagreement is only
    meaningful when the agents saw different evidence) but they share one run_id, so
    their records land together in the store as a single comparable run.

    contradiction_rule (default "symmetric_difference", preserves every existing
    caller and test unchanged) picks which of two independent mechanisms decides
    contradiction_flag / divergent_numbers. concepts_expected_to_overlap only
    affects the "symmetric_difference" rule; it is ignored under "concept_aware".

    "symmetric_difference" — concepts_expected_to_overlap (default True): whether
    the two agents are expected to be reporting on the same underlying question,
    such that a number present in one conclusion and absent from the other is
    itself suspicious. Set this False when the two agents are deliberately
    information-asymmetric by design (e.g. producers/financial.py vs.
    producers/earnings.py — different EDGAR concept sets on purpose, per divij/sdd.md's Open
    Question 1) — see "contradiction_flag semantics" below for what this changes and,
    importantly, what it does NOT fix.

    contradiction_flag semantics, found empirically on 2026-08-29 (see
    divij/model-test-report-2026-08-29.md, Test 5) rather than designed up front:
    with concepts_expected_to_overlap=True (the fixture-test default), any number
    present in one conclusion and absent from the other counts as a contradiction —
    correct for two agents answering the same question (SDD §7's definition-of-done
    edge case explicitly requires this). Applied to genuinely information-asymmetric
    agents, that same rule flagged nearly every real run regardless of whether
    anything actually conflicted (5/5 tickers with any numbers on either side got
    flagged; the one ticker with zero numbers on both sides was the only one that
    didn't). With concepts_expected_to_overlap=False, a number cited by only one side
    no longer contributes to contradiction_flag on its own — this fixes the clearest,
    most common false-positive shape (one agent quantifies something, the other
    simply doesn't address it). It does NOT fix the harder case where both agents
    cite real, correct, non-conflicting numbers about genuinely different concepts
    (e.g. TSLA: Producer A's assets/revenue vs. Producer B's EPS/operating income),
    and it has its own known failure mode: it suppresses a flag whenever either
    side's number set is empty, which silently loses a fabrication on a side with
    no other numbers (the historic AAPL 0.34 debt-to-equity case — see
    divij/cross-agent-validation-disjoint-concepts-diagnosis.md §3a).

    "concept_aware" — delegates to validation/concept_linkage.py's
    contradiction_flag_concept_aware(a_conclusion, b_conclusion): excludes a
    number tagged with a known lens concept from comparison entirely (Producer
    A/B's concept vocabularies are disjoint by construction, so it could never be
    corroborated by the other side), and keeps the old presence/absence rule only
    for untagged numbers. Measured against the real-run corpus
    (tests/test_concept_linkage.py): kills 15 of 16 disjoint-concept false
    positives and preserves the one confirmed true positive (does not share
    "symmetric_difference"'s both-sides-non-empty failure mode, because it never
    imposed that gate to begin with). Still can't tell a legitimate derived ratio
    from a fabrication — both are untagged numbers by the same mechanism. This is
    the rule /api/compare uses for the real Producer A/B pairing as of 2026-09-11;
    it is tuned to that pairing's specific concept vocabulary and is not a general
    solution for an arbitrary third producer.

    shared_concepts (B2): lens concepts BOTH agents were handed, derived by the
    caller from the lens definitions (producers.lens.shared_concepts). Passed to
    "concept_aware", which compares those figures by value instead of excluding
    them. Empty — the default — is lens v1's disjoint pairing.

    given_facts / source_payload (B3): each agent's lens facts
    ({"a": [Fact.to_dict(), ...], "b": [...]}) and the companyfacts payload they came
    from. Used only for structural_flags (validation/constraints.py): whether each
    agent's figures match what it was given, and whether the filing adds up. Absent
    for generic runs, which then get only the per-agent arithmetic checks.

    Returns the comparison result plus every ReasoningObject either agent produced,
    in order (agent A's attempts, then agent B's), including any that halted — ready
    to persist exactly as the /api/chat handler persists a single agent's records.

    confidence_score / data_sources_a / data_sources_b are keyword-only additions
    beyond the SDD signature, passed straight through to run_validation_loop so real
    provenance can be recorded per agent. Defaults match run_validation_loop's own.

    concurrent (default True) runs both agents at once — see the module docstring's
    Concurrency section. on_agent_finished(slot, conclusion, halted), if given, is
    called from the agent's own thread the moment that agent is done ("a" or "b"),
    which is how a live stream can show one agent finishing while the other is
    still working.
    """
    if run_id is None:
        run_id = uuid.uuid4()

    def run_agent(slot, context, agent_id, call_agent_fn, data_sources):
        outcome = _run_one_agent(
            subject, context, run_id, agent_id,
            call_agent_fn, confidence_score, data_sources,
            assess_a_fn if slot == "a" else assess_b_fn,
        )
        if on_agent_finished is not None:
            on_agent_finished(slot, outcome[0], outcome[2])
        return outcome

    run_a = partial(run_agent, "a", context_a, agent_a_id, call_agent_a_fn, data_sources_a)
    run_b = partial(run_agent, "b", context_b, agent_b_id, call_agent_b_fn, data_sources_b)

    # Agent B runs regardless of A's outcome: there is no reason for one agent's
    # structural failure to suppress the other's evidence.
    if concurrent and _max_concurrency() >= 2:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="cross-agent") as pool:
            # One context copy per task: a Context can't be entered by two threads
            # at once, and LangFuse's @observe nesting lives in contextvars.
            future_a = pool.submit(contextvars.copy_context().run, run_a)
            future_b = pool.submit(contextvars.copy_context().run, run_b)
            a_conclusion, a_objects, a_halted = future_a.result()
            b_conclusion, b_objects, b_halted = future_b.result()
    else:
        a_conclusion, a_objects, a_halted = run_a()
        b_conclusion, b_objects, b_halted = run_b()

    reasoning_objects = a_objects + b_objects

    if a_halted and b_halted:
        status = ComparisonStatus.BOTH_HALTED
    elif a_halted:
        status = ComparisonStatus.AGENT_A_HALTED
    elif b_halted:
        status = ComparisonStatus.AGENT_B_HALTED
    else:
        status = ComparisonStatus.COMPARED

    # Report whatever numbers each surviving conclusion carried, even when no
    # comparison was possible — that is observed evidence, not an inferred result.
    a_numbers = _extract_numbers(a_conclusion) if a_conclusion else []
    b_numbers = _extract_numbers(b_conclusion) if b_conclusion else []

    metric_rows: list[dict[str, Any]] | None = None
    if status is ComparisonStatus.COMPARED:
        score, word_overlap, number_overlap = _compute_score(a_conclusion, b_conclusion)
        agreement: ConsistencyAgreement | None = _classify(score)

        # Always computed, whichever rule decides the flag: this is what a reviewer
        # reads figure by figure (the UI's comparison matrix).
        canonical_flag, canonical_divergent, canonical_rows = contradiction_flag_canonical(
            a_conclusion, b_conclusion,
            include_years=include_years, context_a=context_a, context_b=context_b,
        )
        metric_rows = [r.to_dict() for r in canonical_rows]

        if contradiction_rule == "canonical_facts":
            contradiction_flag = canonical_flag
            divergent = canonical_divergent
        elif contradiction_rule == "concept_aware":
            # Delegates entirely to validation/concept_linkage.py: a known-concept
            # number is excluded from comparison (never corroborable by the other
            # side, by construction), an untagged number keeps the old
            # presence/absence rule. divergent_numbers here is the untagged-only
            # difference — the numbers that actually drove the flag — not the full
            # symmetric difference "symmetric_difference" mode reports; see this
            # function's docstring for why those aren't the same set under this rule.
            contradiction_flag, divergent, _tagged = contradiction_flag_concept_aware(
                a_conclusion, b_conclusion, shared_concepts=shared_concepts,
            )
        else:
            a_set, b_set = set(a_numbers), set(b_numbers)
            # Symmetric difference: a number in exactly one conclusion. Always computed
            # and reported in full (divergent_numbers below) regardless of
            # concepts_expected_to_overlap — that parameter only changes what counts as
            # "flaggable", not what's disclosed.
            divergent = sorted(a_set.symmetric_difference(b_set))
            if concepts_expected_to_overlap:
                # Same-question agents: presence-without-absence is itself suspicious
                # (SDD §7's definition-of-done edge case requires this).
                contradiction_flag: bool | None = len(divergent) > 0
            else:
                # Information-asymmetric agents: a number cited by only one side isn't
                # evidence of disagreement on its own — that side simply wasn't asked
                # about it. Only flag when both sides actually cited numbers that don't
                # match. Known remaining gap, not fixed by this: two non-empty,
                # genuinely non-overlapping-concept sets (e.g. assets vs. EPS) still
                # trigger this, since there's no concept linkage to tell "different
                # values for the same thing" apart from "different things". See this
                # function's docstring.
                contradiction_flag = bool(a_set) and bool(b_set) and len(divergent) > 0
    else:
        score = word_overlap = number_overlap = None
        agreement = None
        divergent = []
        contradiction_flag = None

    # Run for whichever agents produced a conclusion, compared or not: an agent
    # misquoting its own input is an error whether or not the other agent halted.
    structural_flags = run_checks(
        a_conclusion, b_conclusion, given_facts=given_facts, source_payload=source_payload,
    )

    def final_assessment(objects: list[ReasoningObject]) -> dict | None:
        ok = [o for o in objects if o.parse_status is ParseStatus.SUCCESS]
        return ok[-1].assessment if ok else None

    synthesis = synthesize(
        metric_rows, structural_flags,
        {"a": final_assessment(a_objects), "b": final_assessment(b_objects)},
    ) if status is ComparisonStatus.COMPARED else None

    result = CrossAgentComparisonResult(
        run_id=run_id,
        subject=subject,
        agent_a_id=agent_a_id,
        agent_b_id=agent_b_id,
        status=status,
        agent_a_conclusion=a_conclusion,
        agent_b_conclusion=b_conclusion,
        agent_a_numbers=a_numbers,
        agent_b_numbers=b_numbers,
        divergent_numbers=divergent,
        contradiction_flag=contradiction_flag,
        word_overlap=word_overlap,
        number_overlap=number_overlap,
        score=score,
        agreement=agreement,
        compared_at=datetime.now(timezone.utc),
        metric_comparisons=metric_rows,
        contradiction_rule=contradiction_rule,
        structural_flags=structural_flags,
        synthesis=synthesis,
    )
    return result, reasoning_objects


# ── Persistence (integration with the accountability store) ────────────────────

def build_run_payload(
    result:            CrossAgentComparisonResult,
    reasoning_objects: list[ReasoningObject],
    *,
    scope: str = "auditor",
) -> tuple[dict[str, Any], Any]:
    """
    Assemble the (payload, RunSession) pair for a cross-agent run.

    The payload mirrors the shape /api/chat already stores, plus one new key,
    "cross_agent_comparison". No schema change is needed: the runs table holds a
    free-form JSON blob (web/db.py), so a new key costs nothing.

    Returns (payload, session) so a caller can inspect or amend either before writing.
    """
    from core.directive import get_active_directive          # local: keeps the import graph flat
    from core.schemas import RunStatus, RunSession
    from web.db import _extract_ticker                  # same normalisation server.py uses

    directive = get_active_directive()
    completed = datetime.now(timezone.utc)
    halted = result.status is not ComparisonStatus.COMPARED

    session = RunSession(
        ticker=_extract_ticker(result.subject),
        directive_version=directive.version,
        directive_text=directive.text,
        run_id=result.run_id,
        status=RunStatus.HALTED if halted else RunStatus.COMPLETE,
        reasoning_objects=tuple(reasoning_objects),
        completed_at=completed,
    )

    payload: dict[str, Any] = {
        "run_id":                 str(result.run_id),
        "subject":                result.subject,
        "scope":                  scope,
        "halted":                 halted,
        "reasoning_objects":      [ro.to_dict(investor_scope=False) for ro in reasoning_objects],
        "session":                session.to_dict(),
        "cross_agent_comparison": result.to_dict(),
    }
    return payload, session


def list_contradictions(
    *, ticker: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """
    Scan the store for persisted runs carrying a flagged contradiction.

    SDD §7.3's stated tradeoff: cross_agent_comparison lives inside the payload
    JSON blob, not its own column, so "every contradiction this month" needs a
    Python-side scan rather than a SQL WHERE clause. This is that scan — the
    smallest fix that closes the "not independently queryable" gap without the
    schema change (a dedicated column/index) SDD §7.3 explicitly defers rather
    than reintroducing here.

    Filters get_runs()'s results in-process; not efficient at large scale (SDD's
    own caveat), but no new dependency, no new table, no migration.

    web.db is imported lazily so this module stays importable (and testable)
    without touching the database layer — the same deferral persist_cross_agent_run
    already uses.
    """
    from web.db import get_runs

    runs = get_runs(ticker=ticker, limit=limit)
    return [
        run for run in runs
        if isinstance(run.get("cross_agent_comparison"), dict)
        and run["cross_agent_comparison"].get("contradiction_flag") is True
    ]


def persist_cross_agent_run(
    result:            CrossAgentComparisonResult,
    reasoning_objects: list[ReasoningObject],
    *,
    scope: str = "auditor",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Write a cross-agent run to the append-only store and return the stored payload.

    Read it back with web.db.get_run(run_id)["cross_agent_comparison"].

    Tradeoff (SDD §7.3): the comparison lives inside the payload JSON blob, so it is
    retrievable by run_id but not queryable in SQL — "every contradiction this month"
    needs a Python-side scan of get_runs() or SQLite's JSON1 extension. Accepted for
    v1; a dedicated column is a later schema decision.

    web.db is imported lazily so this module stays importable (and testable) without
    touching the database layer — the same deferral validation/consistency.py uses for middleware.
    """
    from web.db import init_db, insert_run, insert_session

    payload, session = build_run_payload(result, reasoning_objects, scope=scope)
    # Caller-supplied context for the record (producers, contexts, facts, claims,
    # steps). Until 2026-09-24 a stored compare run kept only 7 keys, so a run
    # reopened from history had no trace and no input facts. Never overwrites a key
    # build_run_payload set.
    for key, value in (extra or {}).items():
        payload.setdefault(key, value)

    init_db()
    insert_run(payload)
    insert_session(str(result.run_id), session.ticker, payload["session"])
    return payload
