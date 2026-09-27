"""
Producer B — the earnings-quality lens.

Reads per-share and operating-income concepts out of the *same* EDGAR companyfacts
payload Producer A reads, and runs them through the accountability pipeline as
`AgentID.EARNINGS`. Closes the v1 stretch goal in
divij/cross-agent-validation-proposal.md §6.4 ("wire in a second real source ... to
replace the fixture").

Until lens v2 (2026-09-25) the concept set was deliberately disjoint from
`producers/financial.py`'s, so the two producers saw genuinely different evidence
about one company — the information asymmetry divij/sdd.md's Open Question #1
argues makes disagreement meaningful. It also meant they could never disagree about
the *same* figure, so every comparison was between different things. v2 keeps most
of the asymmetry and adds two shared figures (NetIncomeLoss, EarningsPerShareDiluted)
that both agents are handed and can be checked against each other.

Both producers now reach EDGAR through `datasources.edgar` rather than this module
importing from its sibling, and both share `producers.lens`'s single runner, so the
only difference between Producer A and Producer B is the four fields below.
"""

from __future__ import annotations

import uuid

from langfuse import observe

from core.contracts import AgentAdapter, JsonFetcher
from core.schemas import AgentID
from pipeline.middleware import ValidationLoopResult
from producers.lens import ConceptLens, run_lens

# Earnings-quality concepts: per-share and operating-income figures rather than
# balance-sheet and top-line revenue. Lens v1 was disjoint from FINANCIAL_LENS by
# design; lens v2 (B2, 2026-09-25) adds NetIncomeLoss, so the two producers share
# NetIncomeLoss and EarningsPerShareDiluted — see producers/financial.py.
EARNINGS_LENS = ConceptLens(
    name="earnings",
    agent_id=AgentID.EARNINGS,
    concepts=(
        "EarningsPerShareDiluted",
        "EarningsPerShareBasic",
        "OperatingIncomeLoss",
        "NetIncomeLoss",
    ),
    header="Earnings-quality snapshot:",
    trace_prefix="llm_call_earnings",
    version="v2",
)


def summarize_earnings_facts(ticker: str, facts: dict) -> str:
    """Producer B's context string. See `ConceptLens.summarize` for the format contract."""
    return EARNINGS_LENS.summarize(ticker, facts)


@observe(name="analyze_earnings")
def analyze_earnings(
    ticker: str,
    cik: str,
    call_agent_fn: AgentAdapter,
    *,
    run_id: uuid.UUID | None = None,
    fetch_fn: JsonFetcher | None = None,
) -> ValidationLoopResult:
    """
    Run Producer B. Thin by design, for the same reason as
    `producers.financial.analyze_ticker`: the LangFuse span name `analyze_earnings`
    is keyed on by existing traces and stays stable across this refactor.
    """
    return run_lens(
        EARNINGS_LENS, ticker, cik, call_agent_fn, run_id=run_id, fetch_fn=fetch_fn
    )
