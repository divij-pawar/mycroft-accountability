"""
SEC EDGAR read client — the subsystem's only network egress.

Extracted from `financial_grader.py`, which had grown three unrelated
responsibilities: it was an HTTP client, a concept summariser, and an agent
orchestrator. Two consequences of that made this split worth doing rather than
just tidy:

  * `earnings_grader.py` had to import `fetch_company_facts` from
    `financial_grader.py` — one producer depending on a sibling producer purely to
    reach shared infrastructure. Both now depend on this module instead, so
    neither knows the other exists.
  * `_latest_value` was duplicated verbatim in both graders. A change to how the
    latest fact is chosen had to be made twice, correctly, or the two producers
    would silently disagree about what "latest" means — which the cross-agent
    comparator would then report as a contradiction between the *agents*.

P2: this is the one module that reaches the network, and even here the fetch is
injectable (`core.contracts.JsonFetcher`) so no test needs it.

ADR: stdlib `urllib` only. `langfuse` is the single deliberate non-stdlib
dependency, and it is confined to `pipeline/observability.py`; the `@observe`
decorators here come from that same deliberate exception and are no-ops when
LangFuse is unconfigured.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal

from langfuse import observe

from core.contracts import JsonFetcher

_TIMEOUT_S = 10
_MAX_BYTES = 10_000_000  # large-cap companyfacts JSON can run ~4MB+; cap the read
# SEC's fair-access policy asks for a real contact in the User-Agent. The default is
# a placeholder; set SEC_USER_AGENT (e.g. "your-app your-email@example.com") in .env.
_DEFAULT_USER_AGENT = "accountability-layer/1.0 audit-research@project.local"

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL_TMPL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


class EdgarFetchError(Exception):
    """Raised when an EDGAR fetch fails: network error, non-200, or bad JSON."""


def http_get_json(url: str) -> dict:
    """Default `JsonFetcher`: real HTTP GET against SEC EDGAR. stdlib urllib only."""
    user_agent = os.environ.get("SEC_USER_AGENT") or _DEFAULT_USER_AGENT
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            body = resp.read(_MAX_BYTES).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise EdgarFetchError(f"Failed to fetch {url}: {exc}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise EdgarFetchError(f"Non-JSON response from {url}: {exc}") from exc


@observe(as_type="tool")
def lookup_cik(ticker: str, fetch_fn: JsonFetcher | None = None) -> str:
    """
    Resolve a ticker to its zero-padded 10-digit SEC CIK via
    company_tickers.json. `fetch_fn` is injectable for tests
    (default: real HTTP GET, no test should hit the network).
    """
    fetch = fetch_fn or http_get_json
    data = fetch(_TICKER_MAP_URL)
    ticker_upper = ticker.upper().strip()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            return f"{int(entry['cik_str']):010d}"
    raise EdgarFetchError(f"Ticker {ticker!r} not found in SEC company_tickers.json")


@observe(as_type="tool")
def fetch_company_facts(
    ticker: str, cik: str, fetch_fn: JsonFetcher | None = None
) -> dict:
    """
    Fetch SEC EDGAR companyfacts JSON for a given ticker/CIK.
    `fetch_fn` is injectable for tests (default: real HTTP GET).
    Traced as a LangFuse tool span (as_type="tool") via this decorator.

    One call returns the whole payload; every producer's concept lens reads a
    different slice of that *same* payload rather than issuing its own fetch.
    """
    fetch = fetch_fn or http_get_json
    url = _FACTS_URL_TMPL.format(cik=cik)
    return fetch(url)


# ── Fact selection ─────────────────────────────────────────────────────────────
# Until 2026-09-24 this module offered only latest_value(): max(end) over every
# entry, returned as a bare float. Against AAPL's real payload that handed agents
# FY2018 revenue as current (a retired tag) and a nine-month year-to-date EPS as if
# it were the quarter (10-Qs tag both durations with the same `end`; max() took
# whichever was listed first). See tests/test_edgar_facts.py, which pins both.

PeriodKind = Literal["instant", "quarter", "annual", "ytd", "unknown"]
Basis = Literal["auto", "instant", "quarter", "annual"]

# Filers retire tags. AAPL last reported us-gaap:Revenues in FY2018 and has used the
# ASC 606 tag since, so reading only the literal lens concept silently returns an
# 8-year-old figure. Every candidate is read; the most recent period wins.
CONCEPT_TAGS: dict[str, tuple[str, ...]] = {
    "Revenues": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ),
}

_QUARTER_DAYS = range(80, 101)    # a fiscal quarter is 13 weeks (91 days), ±
_ANNUAL_DAYS = range(350, 381)    # 52- or 53-week fiscal years

# "auto" preference: a stock concept (Assets) only has instants; a flow concept
# (Revenues, EPS) prefers the latest standalone quarter. Year-to-date cumulative
# figures are a last resort, and are labeled as such when used.
_AUTO_ORDER: tuple[PeriodKind, ...] = ("instant", "quarter", "annual", "unknown", "ytd")


@dataclass(frozen=True)
class Fact:
    """
    One reported value with the metadata that makes it comparable.

    `concept` is what the lens asked for; `tag` is the us-gaap tag the value
    actually came from (they differ when CONCEPT_TAGS fell through to a newer tag).
    fy/fp are the *filer's* fiscal labels (Apple's FY2026 Q3 ends in June);
    `frame` is SEC's calendar-aligned period key (CY2026Q2) — both are kept
    because an agent should cite the former and a comparator can key on the latter.
    """
    concept: str
    tag: str
    value: float
    unit: str | None
    start: str | None
    end: str | None
    fy: int | None
    fp: str | None
    form: str | None
    frame: str | None
    accn: str | None
    filed: str | None
    period_kind: PeriodKind

    @property
    def duration_days(self) -> int | None:
        return _duration_days(self.start, self.end)

    @property
    def period_label(self) -> str:
        if self.period_kind == "instant":
            return f"as of {self.end}"
        if self.period_kind == "quarter":
            return f"3 months ending {self.end}"
        if self.period_kind == "annual":
            return f"12 months ending {self.end}"
        if self.period_kind == "ytd":
            months = round((self.duration_days or 0) / 30.44)
            return f"{months} months ending {self.end} (year-to-date)"
        return "period unknown"

    def describe(self) -> str:
        """`value unit (metadata)` — the value stays first; see producers/lens.py."""
        meta = []
        if self.fy and self.fp:
            meta.append(f"FY{self.fy} {self.fp}")
        meta.append(self.period_label)
        if self.form:
            meta.append(self.form)
        if self.frame:
            meta.append(f"frame {self.frame}")
        if self.tag != self.concept:
            meta.append(f"tag {self.tag}")
        if self.accn:
            meta.append(f"accn {self.accn}")
        unit = f" {self.unit}" if self.unit else ""
        return f"{self.value}{unit} ({', '.join(meta)})"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["period_label"] = self.period_label
        d["duration_days"] = self.duration_days
        return d


def _duration_days(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return None


def _period_kind(entry: dict) -> PeriodKind:
    days = _duration_days(entry.get("start"), entry.get("end"))
    if days is None:
        # No start date: a genuine instant carries filing metadata; an entry with
        # nothing but end/val (hand-built test fixtures) says nothing either way.
        if entry.get("start") is None and (entry.get("form") or entry.get("frame")):
            return "instant"
        return "unknown"
    if days in _QUARTER_DAYS:
        return "quarter"
    if days in _ANNUAL_DAYS:
        return "annual"
    return "ytd"


def select_fact(facts: dict, concept: str, *, basis: Basis = "auto") -> Fact | None:
    """
    The most recent value for `concept` of the requested period kind, or None.

    basis="auto": point-in-time for balance-sheet concepts, else the latest
    standalone quarter, else annual; a year-to-date cumulative figure only if
    that's all the payload has. Among entries of the chosen kind the latest `end`
    wins, then the latest `filed` (a restatement supersedes the original).
    """
    try:
        us_gaap = facts["facts"]["us-gaap"]
    except (KeyError, TypeError):
        return None

    candidates: list[tuple[int, str, str, dict, PeriodKind]] = []
    for priority, tag in enumerate(CONCEPT_TAGS.get(concept, (concept,))):
        units = (us_gaap.get(tag) or {}).get("units") or {}
        for unit, entries in units.items():
            for e in entries:
                if isinstance(e.get("val"), (int, float)) and e.get("end"):
                    candidates.append((priority, tag, unit, e, _period_kind(e)))
    if not candidates:
        return None

    kinds = {c[4] for c in candidates}
    if basis == "auto":
        wanted = next(k for k in _AUTO_ORDER if k in kinds)
    else:
        wanted = basis
    pool = [c for c in candidates if c[4] == wanted]
    if not pool:
        return None

    _, tag, unit, e, kind = max(
        pool, key=lambda c: (c[3]["end"], c[3].get("filed") or "", -c[0])
    )
    return Fact(
        concept=concept,
        tag=tag,
        value=float(e["val"]),
        unit=unit,
        start=e.get("start"),
        end=e.get("end"),
        fy=e.get("fy"),
        fp=e.get("fp"),
        form=e.get("form"),
        frame=e.get("frame"),
        accn=e.get("accn"),
        filed=e.get("filed"),
        period_kind=kind,
    )


def latest_value(facts: dict, concept: str) -> float | None:
    """
    Value of select_fact(facts, concept) — kept for callers that only need the
    number. Prefer select_fact(): a bare float is exactly how the period was lost.
    """
    fact = select_fact(facts, concept)
    return fact.value if fact is not None else None
