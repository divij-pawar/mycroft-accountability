#!/usr/bin/env python3
"""
Manual script: run Cross-Agent Validation with two REAL, independently-reasoned
LLM agents — not the fixture Producer B used in the automated test suite.

Same category as scripts/smoke_langfuse_trace.py: a manual verification script, not
part of `python -m unittest discover`. It makes a real EDGAR fetch and real LLM
calls, so it does not belong in the automated suite's "no network, no live model"
convention.

Producer A (AgentID.FINANCIAL) and Producer B (AgentID.EARNINGS) both read the
same company's live SEC EDGAR companyfacts payload, but through producers/financial.py's
and producers/earnings.py's different concept sets (balance-sheet/revenue vs.
per-share/operating-income) — genuinely different evidence about the same company,
each independently summarized and independently reasoned over by the model. This
closes the "Producer B is a fixture" gap named in
divij/cross-agent-validation-proposal.md's stretch goal (§6.4).

Usage:
    python scripts/run_cross_agent_live.py [TICKER]

Requires GEMINI_API_KEY in .env (see .env.example). Defaults to two different
Gemini model variants (gemini-2.5-flash for Producer A, gemini-2.5-flash-lite
for Producer B) — genuine model heterogeneity (per divij/sdd.md's Open Question
1: does a real comparison need two different *models*, or is two different
*data slices* enough?) without requiring a second provider or a local Ollama
pull. Override with --agent-a-model / --agent-b-model, or pass
--agent-b-provider ollama to use a local Ollama model instead, if one is pulled
(see adapters/ollama_adapter.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Entry-point scripts live in scripts/, one level below the subsystem root; put that
# root on sys.path so `core.*`, `producers.*` etc. resolve whether this file is run as
# `python scripts/<name>.py` or `python -m scripts.<name>`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import find_dotenv, load_dotenv

# Search upward from cwd for a .env file — same call web/server.py already makes.
# Without this, GEMINI_API_KEY sitting in .env is invisible to os.environ and
# gemini_adapter.py fails with "GEMINI_API_KEY environment variable is not set"
# even when the file is correctly filled in.
load_dotenv(find_dotenv(usecwd=True) or find_dotenv())

from langfuse import observe

# Provider-specific exception types only: this script prints a different remediation
# hint for each, so the distinction is load-bearing. Construction comes from the registry.
from adapters.gemini_adapter import RateLimitDailyError, RateLimitMinuteError
from adapters.ollama_adapter import OllamaConnectionError, OllamaModelError
from adapters.registry import build_adapter, provider_names, with_model_override
from validation.cross_validation import persist_cross_agent_run, run_cross_agent_validation
from datasources.edgar import fetch_company_facts, lookup_cik
from producers.earnings import summarize_earnings_facts
from producers.financial import summarize_facts
from pipeline.middleware import HaltError
from pipeline.observability import make_traced_adapter
from core.schemas import AgentID, DataSource, DataSourceStatus


# Deterministic defaults for a manual live run: temperature 0 + a fixed seed, so a
# disagreement between the two producers is attributable to the evidence or the
# model rather than to sampling noise.
_LIVE_DEFAULTS = {"temperature": 0.0, "seed": 42}

# Every registered provider except the mock: this script exists to make real calls, so
# offering "mock" here would only produce a live run that never went anywhere. Derived
# from the registry rather than listed, so a new real provider is usable immediately.
_LIVE_PROVIDERS = tuple(n for n in provider_names() if n != "mock")


def _build_adapter(provider: str, model: str | None = None):
    """
    Build one producer's adapter through adapters/registry.py.

    This function used to be a second copy of web/server.py's provider chain, which
    is how the two entry points could disagree about which config key a model name
    belongs in. Now both route a model override through the registry, so
    `--agent-b-provider ollama --agent-b-model qwen2.5:7b` lands on `ollama_model`
    here for exactly the same reason it does over HTTP.
    """
    return build_adapter(with_model_override(_LIVE_DEFAULTS, provider, model))


def _run_live(
    ticker: str,
    agent_a_provider: str,
    agent_b_provider: str,
    agent_a_model: str | None,
    agent_b_model: str | None,
):
    """
    The whole comparison, as one function, so @observe below gives it a single
    parent trace — the EDGAR tool-span fetch and both producers' LLM generation
    spans all nest under one "cross_agent_validation:{ticker}" trace instead of
    scattering as disconnected top-level traces (or, for the LLM calls, not being
    traced at all — see scripts/run_cross_agent_live.py's module docstring history in
    logs/RUN_LOG.md for why this needed fixing after the first pass).
    """
    cik = lookup_cik(ticker)
    print(f"Resolved {ticker} -> CIK {cik}")

    facts = fetch_company_facts(ticker, cik)
    context_a = summarize_facts(ticker, facts)
    context_b = summarize_earnings_facts(ticker, facts)

    print("\nProducer A context (AgentID.FINANCIAL):")
    print(context_a)
    print("\nProducer B context (AgentID.EARNINGS):")
    print(context_b)

    edgar_source = DataSource(
        source="SEC EDGAR",
        status=DataSourceStatus.LIVE,
        url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
    )

    label_a = f"{agent_a_provider}:{agent_a_model}" if agent_a_model else agent_a_provider
    label_b = f"{agent_b_provider}:{agent_b_model}" if agent_b_model else agent_b_provider
    print(f"\nCalling {label_a} (Producer A) and {label_b} (Producer B) independently...\n")

    # Wrapped in make_traced_adapter, same as analyze_ticker/analyze_earnings do
    # for standalone use — without this, the two real LLM calls (the entire point
    # of this script) would be invisible to LangFuse even though the EDGAR fetch
    # above is traced.
    traced_adapter_a = make_traced_adapter(
        _build_adapter(agent_a_provider, agent_a_model), name=f"llm_call_financial:{ticker}"
    )
    traced_adapter_b = make_traced_adapter(
        _build_adapter(agent_b_provider, agent_b_model), name=f"llm_call_earnings:{ticker}"
    )

    return run_cross_agent_validation(
        ticker,
        context_a,
        context_b,
        AgentID.FINANCIAL,
        AgentID.EARNINGS,
        traced_adapter_a,
        traced_adapter_b,
        data_sources_a=(edgar_source,),
        data_sources_b=(edgar_source,),
        # producers/financial.py vs. producers/earnings.py are deliberately information-asymmetric
        # (different EDGAR concept sets). Same rule /api/compare uses as of
        # 2026-09-11 — see run_cross_agent_validation's docstring for exactly
        # what concept_aware does and doesn't fix.
        contradiction_rule="concept_aware",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker", nargs="?", default="AAPL")
    parser.add_argument("--agent-a-provider", default="gemini", choices=_LIVE_PROVIDERS)
    parser.add_argument("--agent-b-provider", default="gemini", choices=_LIVE_PROVIDERS)
    # No hardcoded default here — a Gemini-specific model name would be wrong
    # for an Ollama producer, and vice versa. Left unset, each adapter falls
    # back to its own default (gemini_adapter.py / ollama_adapter.py). Explicit
    # overrides apply to whichever provider that side actually uses.
    parser.add_argument("--agent-a-model", default=None)
    parser.add_argument("--agent-b-model", default=None)
    args = parser.parse_args()

    ticker = args.ticker.upper()
    print("Cross-Agent Validation — live run")
    print("=" * 60)

    agent_a_model = args.agent_a_model
    agent_b_model = args.agent_b_model
    # Real model heterogeneity out of the box, but only when it makes sense:
    # two different Gemini variants when both sides are Gemini. Mixed or
    # Ollama-only runs use each adapter's own default unless overridden above.
    if args.agent_a_provider == "gemini" and args.agent_b_provider == "gemini":
        agent_a_model = agent_a_model or "gemini-2.5-flash"
        agent_b_model = agent_b_model or "gemini-2.5-flash-lite"

    try:
        traced_run = observe(name=f"cross_agent_validation:{ticker}")(_run_live)
        result, objects = traced_run(
            ticker,
            args.agent_a_provider,
            args.agent_b_provider,
            agent_a_model,
            agent_b_model,
        )

        print("=" * 60)
        print(f"Status:             {result.status.value}")
        print(f"Agent A conclusion: {result.agent_a_conclusion}")
        print(f"Agent B conclusion: {result.agent_b_conclusion}")
        print(f"Contradiction flag: {result.contradiction_flag}")
        print(f"Divergent numbers:  {result.divergent_numbers}")
        print(f"Agreement:          {result.agreement}")
        print(f"Score:              {result.score}")

        stored = persist_cross_agent_run(result, objects)
        print(f"\nPersisted. Retrieve with: get_run({result.run_id!r})['cross_agent_comparison']")
        return 0

    except HaltError as exc:
        print(f"\nHALTED: {exc}")
        return 1
    except (RateLimitDailyError, RateLimitMinuteError) as exc:
        print(f"\nGemini rate limit: {exc}")
        return 1
    except (OllamaConnectionError, OllamaModelError) as exc:
        print(f"\nOllama error: {exc}")
        return 1
    except EnvironmentError as exc:
        print(f"\nConfiguration error: {exc}")
        return 1
    # Catch-all matching web/server.py's /api/chat pattern — without this, an
    # API-side error the adapter doesn't specifically handle (e.g. a suspended
    # or revoked key, a 403/permission error) surfaces as a raw traceback
    # instead of a clear message. Found live: a real key that reached Google's
    # servers but came back "CONSUMER_SUSPENDED" wasn't caught by any branch
    # above (not a rate limit, not a missing-key EnvironmentError).
    except Exception as exc:
        print(f"\nAdapter/API error — {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
