"""
Producer A — the headline-financials lens.

Reads balance-sheet and top-line concepts out of the shared EDGAR companyfacts
payload and runs them through the accountability pipeline as `AgentID.FINANCIAL`.

This module used to be a 156-line file that was simultaneously an HTTP client, a
summariser and an orchestrator. The HTTP client now lives in `datasources.edgar`
and the orchestration in `producers.lens`, which leaves this file holding the only
thing that is actually specific to Producer A: which concepts it reads. That is the
point — a producer should be a declaration, not an implementation.

Not a ratio engine. There is no structured recommendation or target price; the
conclusion is the same free-text `<conclusion>` block every other agent in this
subsystem produces. Ratio calculations, competitor-filing lookups and backtesting
remain out of scope.
"""

from __future__ import annotations

import uuid

from langfuse import observe

from core.contracts import AgentAdapter, JsonFetcher
from core.schemas import AgentID
from pipeline.middleware import ValidationLoopResult
from producers.lens import ConceptLens, run_lens

# Headline us-gaap concepts pulled into the LLM context — not a ratio engine, just
# enough real data to make the synthesis call meaningful. Until 2026-09-25 (lens v1)
# this set was deliberately disjoint from producers/earnings.py's, so the two
# producers never saw a common figure and could never genuinely disagree
# (divij/cross-agent-validation-disjoint-concepts-diagnosis.md). Lens v2 (B2) keeps
# each producer's own emphasis and adds the two headline figures both now share:
# NetIncomeLoss and EarningsPerShareDiluted (see producers.lens.shared_concepts).
# New concepts go last, so every v1 context line keeps its position.
FINANCIAL_LENS = ConceptLens(
    name="financial",
    agent_id=AgentID.FINANCIAL,
    concepts=("Assets", "Revenues", "NetIncomeLoss", "EarningsPerShareDiluted"),
    trace_prefix="llm_call",
    version="v2",
)


def summarize_facts(ticker: str, facts: dict) -> str:
    """Producer A's context string. See `ConceptLens.summarize` for the format contract."""
    return FINANCIAL_LENS.summarize(ticker, facts)


@observe(name="analyze_ticker")
def analyze_ticker(
    ticker: str,
    cik: str,
    call_agent_fn: AgentAdapter,
    *,
    run_id: uuid.UUID | None = None,
    fetch_fn: JsonFetcher | None = None,
) -> ValidationLoopResult:
    """
    Run Producer A. Thin by design: the LangFuse span name `analyze_ticker` is the
    only reason this wrapper exists rather than callers invoking `run_lens` directly
    — existing traces are keyed on that name, so it stays stable across this
    refactor.
    """
    return run_lens(
        FINANCIAL_LENS, ticker, cik, call_agent_fn, run_id=run_id, fetch_fn=fetch_fn
    )
