"""
Concept lenses — one runner, many producers (OCP).

A "producer" in this subsystem is one agent's view of a company: a set of us-gaap
concepts read out of a shared EDGAR payload, summarised into a context string, and
reasoned over by an LLM through the unmodified validation loop. Producer A
(balance-sheet and top-line) and Producer B (per-share and operating income) differ
in exactly four things: which concepts they read, which `AgentID` they run as, an
optional header line, and their LangFuse span name.

Before this module, those four differences were expressed as two near-identical
modules — `financial_grader.py` and `earnings_grader.py` — whose `summarize_*` and
`analyze_*` functions were the same code with different constants, down to a
verbatim copy of `_latest_value`. Adding a third producer meant copying a third
module, so the pipeline was open for extension only by modification of itself.

Now the varying part is data (`ConceptLens`) and the invariant part is code
(`run_lens`). A third producer is a `ConceptLens` literal; no runner changes, and
in particular nothing about ADR-07's retry/halt behaviour can drift between
producers, because there is only one call site for it.

Deliberately *not* generalised: a lens cannot change how the LLM is called, how
parsing works, or what happens on failure. Those are the properties the
accountability layer exists to hold constant across agents, so they are not
parameters.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from core.contracts import AgentAdapter, JsonFetcher
from core.schemas import AgentID
from datasources.edgar import Fact, fetch_company_facts, select_fact
from pipeline.middleware import ValidationLoopResult, run_validation_loop
from pipeline.observability import make_traced_adapter


@dataclass(frozen=True)
class ConceptLens:
    """
    One producer's view of a company: the whole difference between two agents.

    Frozen because a lens is an identity, not a setting. If a run could mutate the
    concept set mid-flight, the `concepts` list reported in a run's audit trail
    would no longer be evidence of what that agent actually read (P3).
    """

    name: str                          # short id used in logs and the UI, e.g. "financial"
    agent_id: AgentID                  # identity every run under this lens is attributed to
    concepts: tuple[str, ...]          # us-gaap concepts this producer reads, in context order
    header: str | None = None          # optional second line of context, before the concepts
    trace_prefix: str = "llm_call"     # LangFuse generation-span prefix for this producer
    # Which concept set this is. Stored with every compare run (producers[slot].lens_version)
    # so a replay or a corpus entry says which pairing produced it: "v1" was the
    # deliberately disjoint A/B pairing (2026-08-28 to 2026-09-25); "v2" (B2,
    # 2026-09-25) adds NetIncomeLoss and EarningsPerShareDiluted to both lenses.
    version: str = "v1"

    def select(self, facts: dict) -> list[Fact | None]:
        """This lens's facts, in concept order; None where the payload has none."""
        return [select_fact(facts, concept) for concept in self.concepts]

    def summarize(self, ticker: str, facts: dict) -> str:
        """
        Short plain-text context string: one `Concept: value unit (period, ...)`
        line per concept this lens reads, e.g.
        `EarningsPerShareDiluted: 2.02 USD/shares (FY2026 Q3, 3 months ending
        2026-06-27, 10-Q, frame CY2026Q2, accn ...)`.

        The shape is load-bearing beyond readability — the UI's input-provenance
        check parses the leading number after the first `: ` back out to ask
        whether a number the agent cited traces to a value it was actually given,
        so the value always comes first and the metadata after it.

        A concept absent from the payload renders as `not reported` rather than
        being dropped, so the context shows what the agent was *asked* to consider
        and not merely what happened to exist.
        """
        lines = [f"Ticker: {ticker}"]
        if self.header:
            lines.append(self.header)
        for concept, fact in zip(self.concepts, self.select(facts)):
            lines.append(f"{concept}: {fact.describe() if fact is not None else 'not reported'}")
        return "\n".join(lines)


def shared_concepts(*lenses: ConceptLens) -> frozenset[str]:
    """
    Concepts every given lens reads — the ones two agents can genuinely agree or
    disagree about. Derived from the lens definitions at call time, so a comparator
    never carries its own copy of either concept list (validation/concept_linkage.py
    used to assume the two were disjoint forever).
    """
    if not lenses:
        return frozenset()
    common = set(lenses[0].concepts)
    for lens in lenses[1:]:
        common &= set(lens.concepts)
    return frozenset(common)


def run_lens(
    lens: ConceptLens,
    ticker: str,
    cik: str,
    call_agent_fn: AgentAdapter,
    *,
    run_id: uuid.UUID | None = None,
    fetch_fn: JsonFetcher | None = None,
) -> ValidationLoopResult:
    """
    Run one producer end to end:
      1. Fetch the EDGAR companyfacts payload (traced LangFuse tool call).
      2. Summarise it through `lens`.
      3. Run the unmodified validation loop as `lens.agent_id`, with the supplied
         adapter wrapped so each LLM attempt is its own traced generation span.

    Returns whatever `run_validation_loop` returns, and raises `HaltError` exactly
    as it does on double failure — this wrapper adds no error handling of its own,
    because swallowing a halt here would defeat ADR-07.
    """
    if run_id is None:
        run_id = uuid.uuid4()

    facts = fetch_company_facts(ticker, cik, fetch_fn=fetch_fn)
    context = lens.summarize(ticker, facts)

    traced_adapter = make_traced_adapter(
        call_agent_fn, name=f"{lens.trace_prefix}:{ticker}"
    )

    return run_validation_loop(
        ticker,
        context,
        run_id,
        lens.agent_id,
        call_agent_fn=traced_adapter,
    )
