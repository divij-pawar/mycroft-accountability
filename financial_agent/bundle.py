"""
The input bundle: the raw data one analysis was allowed to see, frozen and hashed.

Why this exists
    Re-verifying an analysis later (PLAN.md D6) means running agent B on what agent A saw, not
    on whatever the price source says today. Otherwise a disagreement could just be the data
    changing underneath them. `web/server.py`'s replay route has exactly that gap for chat
    runs: it replays on the subject alone, with "" as context.

What is frozen, and what isn't (refined from PLAN.md 4.5 on 2026-10-06)
    The bundle holds the **data**: the price snapshot (financial_agent/prices.py), the
    selected EDGAR facts, and (phase 6) the document chunks. Its hash covers only that data,
    so the same data fetched twice is the same bundle, and agent B can ask different questions
    of it than agent A did.

    The **tool calls** are not in the bundle. They belong to a run, and live in that run's
    analysis record, each with the hash of the result it returned. financial_agent/replay.py
    can recompute any call from the bundle, so a recorded result that doesn't match its data
    is detectable.

The filing facts are a selection, not the whole companyfacts payload (about 4 MB for a large
filer). Each concept goes through datasources/edgar.py's select_fact, the same period-aware
rule the compare runs use, and the full Fact metadata (period, form, accession) is kept.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.analysis import BUNDLE_SCHEMA, content_hash
from core.contracts import JsonFetcher
from datasources.edgar import fetch_company_facts, lookup_cik, select_fact

# The union of the financial and earnings lenses' concepts (producers/), plus the
# balance-sheet and cash-flow lines an analysis is most likely to want.
FILING_CONCEPTS = (
    "Revenues",
    "GrossProfit",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "NetCashProvidedByUsedInOperatingActivities",
)


class BundleError(ValueError):
    pass


def filing_snapshot(ticker: str, facts: dict, cik: str | None = None) -> dict:
    """
    The selected facts from one companyfacts payload. Concepts the filer doesn't report are
    listed as missing; facts older than the newest period among them are marked stale.

    Why "stale" exists (found live, 2026-10-06): select_fact prefers a standalone quarter to a
    year-to-date figure. Apple reports operating cash flow only year-to-date in its 10-Qs, so
    the one standalone quarter is fiscal Q1, and the selection came back "3 months ending
    2025-12-27" while every other fact ended 2026-06-27. The rule is shared with the compare
    runs and isn't changed here. The fact is kept, marked, and listed, so neither the agent nor
    a reviewer can mistake it for the current quarter.
    """
    selected, missing = [], []
    for concept in FILING_CONCEPTS:
        fact = select_fact(facts, concept)
        if fact is None:
            missing.append(concept)
        else:
            selected.append(fact.to_dict())
    latest = max((f["end"] for f in selected), default=None)
    stale = []
    for f in selected:
        f["stale"] = f["end"] < latest
        if f["stale"]:
            stale.append(f["concept"])
    return {"ticker": ticker, "cik": cik or facts.get("cik"), "entity": facts.get("entityName"),
            "source": "sec-edgar-companyfacts", "latest_period_end": latest,
            "facts": selected, "missing": missing, "stale": stale}


def fetch_filing_snapshot(ticker: str, fetch_fn: JsonFetcher | None = None) -> dict:
    """Live: look up the CIK and fetch companyfacts (datasources/edgar.py), then select."""
    cik = lookup_cik(ticker, fetch_fn=fetch_fn)
    return filing_snapshot(ticker, fetch_company_facts(ticker, cik, fetch_fn=fetch_fn), cik)


def build_bundle(*, ticker: str, prices: dict, filing: dict | None = None,
                 documents: list[dict] | None = None, created_at: str | None = None) -> dict:
    """Freeze the data. `filing` may be None (EDGAR unreachable, or a ticker SEC doesn't list): the agent is told so."""
    if prices.get("ticker") != ticker:
        raise BundleError(f"price snapshot is for {prices.get('ticker')!r}, not {ticker!r}")
    if filing is not None and filing.get("ticker") != ticker:
        raise BundleError(f"filing snapshot is for {filing.get('ticker')!r}, not {ticker!r}")
    data = {
        "schema": BUNDLE_SCHEMA,
        "ticker": ticker,
        "as_of": prices["as_of"],
        "prices": prices,
        "filing": filing,
        "documents": list(documents or []),
    }
    return {
        "bundle_hash": content_hash(data),
        "created_at": created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": data,
    }


def bundle_problems(bundle: dict) -> list[str]:
    """Why this bundle can't be trusted as stored, or [] if it can."""
    if not isinstance(bundle, dict) or not isinstance(bundle.get("data"), dict):
        return ["not a bundle: no data object"]
    problems = []
    data = bundle["data"]
    if data.get("schema") != BUNDLE_SCHEMA:
        problems.append(f"unknown bundle schema {data.get('schema')!r}")
    try:
        actual = content_hash(data)
    except (TypeError, ValueError) as exc:
        return problems + [f"bundle data can't be hashed: {exc}"]
    if actual != bundle.get("bundle_hash"):
        problems.append(f"bundle hash mismatch: stored {bundle.get('bundle_hash')}, data hashes to {actual}")
    for name, series in ((data.get("prices") or {}).get("series") or {}).items():
        if content_hash(series.get("rows")) != series.get("rows_sha256"):
            problems.append(f"{name} price rows don't match their recorded hash")
    return problems
