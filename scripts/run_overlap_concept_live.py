#!/usr/bin/env python3
"""
Manual script: the one live test the corpus has never run — two independently-
reasoning agents given the SAME concept set (not producers/financial.py vs.
producers/earnings.py's deliberately disjoint ones), to find out whether
validation/cross_validation.py's contradiction_flag can ever fire correctly on
a genuine two-agent disagreement about the same fact.

Why this script exists
    divij/cross-agent-validation-disjoint-concepts-diagnosis.md §4: "Nothing in
    31 stored runs has ever put the two producers in a position to genuinely
    disagree, because their concept vocabularies never intersect by
    construction. ... The one test that would actually exercise this is a live
    run where both producers are deliberately asked about an overlapping
    concept." This is that script. It is not part of the automated suite (same
    category as scripts/run_cross_agent_live.py) because it makes a real EDGAR
    fetch and real LLM calls.

Design
    Both sides read the IDENTICAL context string — FINANCIAL_LENS's own
    Assets/Revenues/NetIncomeLoss summary of one company's real EDGAR
    companyfacts payload — so any numeric divergence between them can only come
    from how each model reasoned over (or transcribed) that evidence, not from
    being asked about different things. Run under AgentID.FINANCIAL (the
    existing identity) and AgentID.EXTERNAL ("any third-party or
    provider-agnostic agent", core/schemas.py) standing in for a second,
    independent reviewer of the same evidence — not a new producer module,
    because the concept set is not this script's variable.

    Defaults to two different LOCAL Ollama models (qwen2.5:7b and mistral-7b)
    rather than Gemini, because a real key was never confirmed working
    (web/self_report.py's gemini-key-unconfirmed) and both those models are
    already pulled on this machine — this gets genuine model heterogeneity
    (divij/sdd.md's Open Question 1) without depending on that. Override with
    --agent-a-model / --agent-b-model / --agent-a-provider / --agent-b-provider.

    concepts_expected_to_overlap=True is the whole point here — unlike
    run_cross_agent_live.py, which deliberately sets it False because Producer
    A/B's vocabularies never overlap, these two calls are ABOUT the same
    concepts by construction, so a number one side states and the other omits
    is exactly the "presence without absence is suspicious" case SDD §7
    defines contradiction around.

What this can and cannot show
    A run (or several) coming back unflagged does not prove the mechanism
    works — it may mean both models transcribed the input correctly, which is
    also the likely outcome for two well-behaved deterministic-decoding local
    models reading a short, unambiguous context. A flagged run is the more
    informative result either way: it is either the first observed instance of
    a genuine cross-agent numeric disagreement, or a bug in how one side
    transcribed the identical input — both are new evidence this subsystem has
    not had before. Record whichever happens; do not describe an unflagged
    result as "the tool works" without having seen it catch something.

Usage:
    python scripts/run_overlap_concept_live.py [TICKER ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse

from adapters.ollama_adapter import OllamaConnectionError, OllamaModelError
from adapters.registry import build_adapter, provider_names, with_model_override
from core.schemas import AgentID, DataSource, DataSourceStatus
from datasources.edgar import fetch_company_facts, lookup_cik
from pipeline.middleware import HaltError
from producers.financial import summarize_facts
from validation.cross_validation import persist_cross_agent_run, run_cross_agent_validation

_LIVE_DEFAULTS = {"temperature": 0.0, "seed": 42}
_LIVE_PROVIDERS = tuple(n for n in provider_names() if n != "mock")


def _build_adapter(provider: str, model: str | None):
    return build_adapter(with_model_override(_LIVE_DEFAULTS, provider, model))


def _run_one_ticker(
    ticker: str,
    agent_a_provider: str, agent_a_model: str | None,
    agent_b_provider: str, agent_b_model: str | None,
) -> int:
    print(f"\n{'=' * 60}\n{ticker}\n{'=' * 60}")

    cik = lookup_cik(ticker)
    facts = fetch_company_facts(ticker, cik)
    # ONE context, read by BOTH sides — the deliberate difference from
    # run_cross_agent_live.py, where context_a and context_b come from
    # different lenses over the same payload.
    context = summarize_facts(ticker, facts)
    print("Shared context (both agents read this verbatim):")
    print(context)

    edgar_source = DataSource(
        source="SEC EDGAR",
        status=DataSourceStatus.LIVE,
        url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
    )

    label_a = f"{agent_a_provider}:{agent_a_model}" if agent_a_model else agent_a_provider
    label_b = f"{agent_b_provider}:{agent_b_model}" if agent_b_model else agent_b_provider
    print(f"\nAgent A ({label_a}, AgentID.FINANCIAL) and "
          f"Agent B ({label_b}, AgentID.EXTERNAL) independently reasoning "
          f"over the identical evidence...\n")

    try:
        result, objects = run_cross_agent_validation(
            ticker, context, context,
            AgentID.FINANCIAL, AgentID.EXTERNAL,
            _build_adapter(agent_a_provider, agent_a_model),
            _build_adapter(agent_b_provider, agent_b_model),
            data_sources_a=(edgar_source,),
            data_sources_b=(edgar_source,),
            concepts_expected_to_overlap=True,
        )
    except HaltError as exc:
        print(f"HALTED: {exc}")
        return 1

    print(f"Status:             {result.status.value}")
    print(f"Agent A conclusion: {result.agent_a_conclusion}")
    print(f"Agent B conclusion: {result.agent_b_conclusion}")
    print(f"Contradiction flag: {result.contradiction_flag}")
    print(f"Divergent numbers:  {result.divergent_numbers}")
    print(f"Agreement:          {result.agreement}  (score={result.score})")

    stored = persist_cross_agent_run(result, objects)
    print(f"Persisted run_id={result.run_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tickers", nargs="*", default=["AAPL"])
    parser.add_argument("--agent-a-provider", default="ollama", choices=_LIVE_PROVIDERS)
    parser.add_argument("--agent-b-provider", default="ollama", choices=_LIVE_PROVIDERS)
    parser.add_argument("--agent-a-model", default="qwen2.5:7b")
    parser.add_argument("--agent-b-model", default="mistral-7b")
    args = parser.parse_args()

    print("Cross-Agent Validation — overlapping-concept live test")
    print("(both agents read the SAME concepts; see this script's docstring)")

    worst = 0
    for ticker in args.tickers:
        try:
            rc = _run_one_ticker(
                ticker.upper(),
                args.agent_a_provider, args.agent_a_model,
                args.agent_b_provider, args.agent_b_model,
            )
        except (OllamaConnectionError, OllamaModelError) as exc:
            print(f"Ollama error on {ticker}: {exc}")
            rc = 1
        except EnvironmentError as exc:
            print(f"Configuration error on {ticker}: {exc}")
            rc = 1
        except Exception as exc:
            print(f"Adapter/API error on {ticker} — {type(exc).__name__}: {exc}")
            rc = 1
        worst = max(worst, rc)
    return worst


if __name__ == "__main__":
    sys.exit(main())
