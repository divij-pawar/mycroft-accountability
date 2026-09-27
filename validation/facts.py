"""
Canonical facts — compare two agents' figures by *what* they measure and *when* (B1).

Why this exists
    Every earlier rule compared bare number strings. symmetric_difference flags any
    number present on one side only; concept_aware (validation/concept_linkage.py)
    excludes numbers tagged with a known lens concept and keeps presence/absence
    for the rest. Neither can say "both agents cite diluted EPS for Q3 FY26 and
    their values differ" — the one statement a reviewer actually needs — and both
    misread things that aren't claims about the subject at all (a model's own
    "Confidence level: 90%" flagged NVDA as a conflict on 2026-09-24).

What it does
    extract_facts() turns a conclusion into CanonicalFacts: each quantitative token
    tagged with a canonical metric (by the nearest alias in the same clause), a
    fiscal period (by the nearest period expression in the same sentence), and a
    normalized value. compare_facts() pairs the two agents' facts per metric and
    labels every pairing with one status:

      MATCH                same metric, compatible period, values within tolerance
      MISMATCH             same metric, compatible period, values outside tolerance
      DIFFERENT_PERIODS    same metric, but the periods don't line up
      UNVERIFIABLE_PERIOD  same metric, one side states a period and the other doesn't
      ONE_SIDED            a reported (lens) metric only one agent cites — expected,
                           when only that agent's lens carries it
      CITED_BY_ONE         a metric BOTH agents were handed (lens v2's shared
                           figures) that only one of them cites — not a conflict,
                           just one agent not mentioning it
      UNCORROBORATED       a derived or unrecognised figure only one agent cites —
                           nothing in either agent's input backs it up

      DERIVED_OK           a derived ratio only one agent cites, recomputed from that
                           agent's own figures or inputs — and it checks out
      DERIVED_WRONG        the same, but the arithmetic doesn't check out (an internal
                           error in one agent, not a disagreement between two)

    MISMATCH and UNCORROBORATED raise contradiction_flag. UNCORROBORATED is
    deliberate: it is concept_aware's presence/absence rule for untagged numbers,
    kept because it is what catches the corpus's one confirmed fabrication (AAPL's
    invented "debt-to-equity ratio of 0.34", which the other agent never mentions
    and which nothing it was given could produce). A one-sided ratio that *can* be
    recomputed is checked instead of flagged: found on 2026-09-24 that the corpus's
    "false positives" include two AAPL runs whose "asset turnover of 0.13" is wrong
    by 5x (the same conclusion states revenue $265.6B and assets $383.3B → 0.69),
    next to a net margin that is right. DERIVED_WRONG surfaces that without calling
    it a cross-agent contradiction — it is B3's internal-consistency territory.

What it does NOT do
    Decide which agent is right. Check a figure against the filing (that is B3's
    claim-vs-source check). Link a pronoun or an implied subject to a metric — the
    alias match is lexical, and an unrecognised phrasing leaves a figure untagged.

Periods when nobody states one
    Agents rarely write the period next to every figure. Both agents in one run are
    handed the same filing period, so if *neither* states a period for a metric the
    values are compared as referring to that shared period (and the row says so).
    If only one side states a period, the values are not compared.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Callable, Literal

from core.numeric import QUANTITATIVE_RE, close_enough, normalize_number

# "market": market data (price, market cap) — never in a filing lens, so a figure of
# this family can only have come from outside the agent's input.
Family = Literal["currency", "per_share", "percent_change", "derived", "market", "year", "untagged", "self_report"]
Status = Literal[
    "MATCH", "MISMATCH", "DIFFERENT_PERIODS", "UNVERIFIABLE_PERIOD", "ONE_SIDED", "UNCORROBORATED",
    "DERIVED_OK", "DERIVED_WRONG", "CITED_BY_ONE",
]
PeriodKind = Literal["quarter", "annual", "ytd", "ttm", "unknown"]

FLAGGING_STATUSES: frozenset[str] = frozenset({"MISMATCH", "UNCORROBORATED"})


# ── Metric dictionary ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MetricDef:
    name: str                      # canonical key
    label: str                     # plain-language label for the UI
    family: Family
    aliases: tuple[str, ...]       # lowercase prose phrasings, incl. raw XBRL tag names
    xbrl_tags: tuple[str, ...] = ()


METRICS: tuple[MetricDef, ...] = (
    MetricDef("revenue", "Revenue", "currency",
              ("revenue", "revenues", "net sales", "total sales", "top line", "top-line", "turnover",
               "revenuefromcontractwithcustomerexcludingassessedtax"),
              ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet")),
    MetricDef("gross_profit", "Gross profit", "currency", ("gross profit", "grossprofit"), ("GrossProfit",)),
    MetricDef("operating_income", "Operating income", "currency",
              ("operating income", "operating loss", "income from operations", "operatingincomeloss"),
              ("OperatingIncomeLoss",)),
    MetricDef("net_income", "Net income", "currency",
              ("net income", "net loss", "net earnings", "netincomeloss"), ("NetIncomeLoss",)),
    MetricDef("eps_diluted", "Diluted EPS", "per_share",
              ("diluted eps", "eps diluted", "diluted earnings per share", "earnings per share diluted",
               "earningspersharediluted"), ("EarningsPerShareDiluted",)),
    MetricDef("eps_basic", "Basic EPS", "per_share",
              ("basic eps", "eps basic", "basic earnings per share", "earnings per share basic",
               "earningspersharebasic"), ("EarningsPerShareBasic",)),
    MetricDef("eps", "EPS (basic or diluted not stated)", "per_share", ("eps", "earnings per share")),
    MetricDef("operating_cash_flow", "Operating cash flow", "currency",
              ("operating cash flow", "cash from operations", "cash flow from operations",
               "net cash provided by operating activities"), ("NetCashProvidedByUsedInOperatingActivities",)),
    MetricDef("capex", "Capital expenditures", "currency",
              ("capital expenditures", "capital expenditure", "capex", "purchases of property"),
              ("PaymentsToAcquirePropertyPlantAndEquipment",)),
    MetricDef("free_cash_flow", "Free cash flow", "currency", ("free cash flow", "fcf")),
    MetricDef("current_assets", "Current assets", "currency", ("current assets",), ("AssetsCurrent",)),
    MetricDef("total_assets", "Total assets", "currency", ("total assets", "assets"), ("Assets",)),
    MetricDef("current_liabilities", "Current liabilities", "currency", ("current liabilities",),
              ("LiabilitiesCurrent",)),
    MetricDef("total_liabilities", "Total liabilities", "currency", ("total liabilities", "liabilities"),
              ("Liabilities",)),
    MetricDef("equity", "Shareholders' equity", "currency",
              ("shareholders' equity", "stockholders' equity", "shareholders equity", "stockholders equity",
               "total equity"), ("StockholdersEquity",)),
    # Derived: computed by an agent, never handed to it — the fabrication-risk bucket.
    MetricDef("gross_margin", "Gross margin", "derived", ("gross margin",)),
    MetricDef("operating_margin", "Operating margin", "derived", ("operating margin",)),
    MetricDef("net_margin", "Net margin", "derived", ("net margin", "profit margin", "net profit margin")),
    MetricDef("debt_to_equity", "Debt-to-equity", "derived", ("debt-to-equity", "debt to equity")),
    MetricDef("return_on_equity", "Return on equity", "derived", ("return on equity", "roe")),
    MetricDef("return_on_assets", "Return on assets", "derived", ("return on assets", "roa")),
    MetricDef("current_ratio", "Current ratio", "derived", ("current ratio",)),
    MetricDef("asset_turnover", "Asset turnover", "derived", ("asset turnover",)),
    MetricDef("market_cap", "Market capitalization", "market",
              ("market cap", "market capitalization", "market capitalisation", "market value")),
    MetricDef("share_price", "Share price", "market",
              ("share price", "stock price", "trading at", "closed at", "shares traded at", "the stock")),
    # A model's rating of itself is not a claim about the subject. Tagged so it can
    # be excluded, never compared.
    MetricDef("self_report", "Agent's self-rated confidence", "self_report",
              ("confidence", "confident", "certainty")),
    # Generic (non-financial) subjects — only used when years are enabled.
    MetricDef("release_year", "Release year", "year",
              ("released", "release", "premiered", "came out", "launched", "debuted")),
    MetricDef("founding_year", "Founding year", "year", ("founded", "established", "incorporated")),
)

_BY_NAME: dict[str, MetricDef] = {m.name: m for m in METRICS}
_UNTAGGED = MetricDef("untagged", "Unrecognised figure", "untagged", ())
_YEAR = MetricDef("year", "Year", "year", ())
_LENS_FAMILIES = frozenset({"currency", "per_share"})
_CHANGE_WORDS = re.compile(
    r"\b(up|down|increase[ds]?|decrease[ds]?|grew|growth|rose|fell|declined?|jump(?:ed)?|"
    r"slid|slipped|dropped|gained|climbed|surged|plunged|soared|"
    r"year over year|year-over-year|yoy|from a year|compared to|versus)\b"
)
# "$2.02 per share" is a per-share figure whatever word precedes it — live AAPL run,
# 2026-09-24: "net income loss of $2.02 per share" and "earnings were $2.02 per
# share" are the same EPS, not net income and an unrecognised figure.
_PER_SHARE_AFTER = re.compile(r"\s*(?:per|a|/)\s*(?:diluted\s+|basic\s+)?share\b")

# Per-family tolerance: (kind, amount). Named here with the reason, not scattered.
TOLERANCE: dict[str, tuple[str, float]] = {
    "currency":       ("rel", 0.005),   # $109.4B vs $109.417B is the same figure, rounded
    "per_share":      ("abs", 0.005),   # EPS is reported to the cent; $2.02 vs $2.03 is a real miss
    "percent_change": ("abs", 0.1),     # 16% vs 16.05% is rounding; 16% vs 17% is not
    "derived":        ("rel", 0.01),
    "market":         ("rel", 0.01),
    "untagged":       ("rel", 0.01),
    "year":           ("abs", 0.0),
}


def _alias_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])")


_ALIAS_PATTERNS: tuple[tuple[MetricDef, str, re.Pattern[str]], ...] = tuple(
    (m, a, _alias_pattern(a)) for m in METRICS for a in m.aliases
)


# ── Periods ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Period:
    kind: PeriodKind = "unknown"
    fy: int | None = None
    fq: int | None = None

    @property
    def known(self) -> bool:
        return self.kind != "unknown"

    def label(self) -> str | None:
        if self.kind == "quarter":
            return f"Q{self.fq} FY{self.fy}" if self.fy else f"Q{self.fq}"
        if self.kind == "annual":
            return f"FY{self.fy}"
        if self.kind == "ytd":
            return f"year-to-date FY{self.fy}" if self.fy else "year-to-date"
        if self.kind == "ttm":
            return "trailing twelve months"
        return None

    def compatible(self, other: "Period") -> bool:
        """Same period, treating a missing fiscal year as 'not stated', not 'different'."""
        if self.kind != other.kind or self.fq != other.fq:
            return False
        return self.fy is None or other.fy is None or self.fy == other.fy


UNKNOWN = Period()
_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def _year(s: str | None) -> int | None:
    if not s:
        return None
    n = int(s)
    return n + 2000 if n < 100 else n


_PERIOD_PATTERNS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], Period]], ...] = (
    (re.compile(r"\bq([1-4])\s*(?:fy\s*)?'?(\d{4}|\d{2})\b"),
     lambda m: Period("quarter", _year(m.group(2)), int(m.group(1)))),
    (re.compile(r"\b(?:fy|fiscal(?:\s+year)?)\s*'?(\d{4}|\d{2})\s*q([1-4])\b"),
     lambda m: Period("quarter", _year(m.group(1)), int(m.group(2)))),
    (re.compile(r"\b(first|second|third|fourth)\s+(?:fiscal\s+)?quarter(?:\s+of)?(?:\s+(?:fiscal\s+|fy\s*)?(\d{4}))?"),
     lambda m: Period("quarter", _year(m.group(2)), _ORDINAL[m.group(1)])),
    (re.compile(r"\bq([1-4])\b"), lambda m: Period("quarter", None, int(m.group(1)))),
    (re.compile(r"\b(?:ttm|ltm|trailing\s+(?:twelve|12)\s+months|last\s+(?:twelve|12)\s+months)\b"),
     lambda m: Period("ttm")),
    (re.compile(r"\b(?:nine|six)\s+months\s+ended\b|\byear[-\s]to[-\s]date\b|\bytd\b"),
     lambda m: Period("ytd")),
    (re.compile(r"\b(?:fy|fiscal(?:\s+year)?)\s*'?(\d{4}|\d{2})\b"),
     lambda m: Period("annual", _year(m.group(1)))),
)


def _periods_in(sentence: str) -> list[tuple[int, int, Period]]:
    """Every period expression in a lowercased sentence, first pattern to claim a span wins."""
    found: list[tuple[int, int, Period]] = []
    for pattern, build in _PERIOD_PATTERNS:
        for m in pattern.finditer(sentence):
            if any(s < m.end() and m.start() < e for s, e, _ in found):
                continue
            found.append((m.start(), m.end(), build(m)))
    return found


# ── Extraction ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CanonicalFact:
    metric: str
    label: str
    family: Family
    value: float
    raw: str
    period: Period
    sentence: str
    span: tuple[int, int]       # character offsets of `raw` in the conclusion

    def to_dict(self) -> dict:
        d = asdict(self)
        d["period"] = self.period.label()
        return d


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_BREAK_RE = re.compile(r"[;,]|\s(?:and|while|whereas|but)\s")
# Citations and URLs are masked before extraction: a "1.5" inside a URL or a
# [SOURCE: ...] bracket is not a figure the agent is asserting.
_MASK_RE = re.compile(r"\[SOURCE:[^\]]*\]|https?://\S+", re.IGNORECASE)
# A sentence-ending "." after a year is fine ("released in 2010."); a decimal or
# thousands continuation is not ("2010.5", "2,010,000").
_BARE_YEAR_RE = re.compile(r"(?<![\d.,$])\b(1[89]\d{2}|20\d{2})\b(?![\d%]|[.,]\d)")


def _mask(text: str) -> str:
    return _MASK_RE.sub(lambda m: " " * len(m.group(0)), text)


def _sentences(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for piece in _SENTENCE_SPLIT_RE.split(text):
        if piece:
            start = text.index(piece, pos)
            out.append((start, piece))
            pos = start + len(piece)
    return out


def _clause_bounds(sentence: str, start: int, end: int) -> tuple[int, int]:
    lo, hi = 0, len(sentence)
    for m in _CLAUSE_BREAK_RE.finditer(sentence):
        if m.end() <= start:
            lo = m.end()
        elif m.start() >= end:
            hi = m.start()
            break
    return lo, hi


def _metric_for(sentence: str, start: int, end: int, *, years: bool) -> MetricDef | None:
    """
    Nearest alias ending before the figure in the same clause; else the nearest one
    after it in the same clause; else the nearest one earlier in the sentence. Ties
    at the same position go to the longer alias ("diluted eps" beats "eps").
    """
    lo, hi = _clause_bounds(sentence, start, end)
    before, after, earlier = [], [], []
    for metric, alias, pattern in _ALIAS_PATTERNS:
        if (metric.family == "year") != years:
            continue
        for m in pattern.finditer(sentence):
            if m.end() <= start:
                (before if m.start() >= lo else earlier).append((start - m.end(), -len(alias), metric))
            elif m.start() >= end and m.end() <= hi:
                after.append((m.start() - end, -len(alias), metric))
    for bucket in (before, after, earlier):
        if bucket:
            return min(bucket, key=lambda t: (t[0], t[1]))[2]
    return None


def _nearest_period(periods: list[tuple[int, int, Period]], start: int, end: int) -> Period:
    if not periods:
        return UNKNOWN
    def distance(p):
        s, e, _ = p
        return start - e if e <= start else (s - end if s >= end else 0)
    return min(periods, key=distance)[2]


def extract_facts(conclusion: str, *, include_years: bool = False) -> list[CanonicalFact]:
    """
    Every figure in `conclusion` as a CanonicalFact. Self-rated confidence figures
    are dropped. With include_years, bare 4-digit years (invisible to
    core/numeric.py's QUANTITATIVE_RE) become `year`-family facts — for generic,
    non-financial subjects, where "released in 2010" is the claim.
    """
    if not conclusion:
        return []
    masked = _mask(conclusion)
    facts: list[CanonicalFact] = []
    for sent_start, sentence in _sentences(masked):
        lower = sentence.lower()
        periods = _periods_in(lower)
        candidates: list[tuple[int, int, str, bool]] = [
            (m.start(), m.end(), m.group(0).strip(), False) for m in QUANTITATIVE_RE.finditer(sentence)
        ]
        if include_years:
            taken = [(s, e) for s, e, _, _ in candidates] + [(s, e) for s, e, _ in periods]
            for m in _BARE_YEAR_RE.finditer(sentence):
                if not any(s < m.end() and m.start() < e for s, e in taken):
                    candidates.append((m.start(), m.end(), m.group(0), True))

        for start, end, raw, is_year in candidates:
            value = float(raw) if is_year else normalize_number(raw)
            if value is None:
                continue
            metric = _metric_for(lower, start, end, years=is_year) or (_YEAR if is_year else _UNTAGGED)
            if metric.family == "self_report":
                continue
            if not is_year and _PER_SHARE_AFTER.match(lower, end) and metric.family != "per_share":
                metric = _BY_NAME["eps"]
            family: Family = metric.family
            name, label = metric.name, metric.label
            lo, hi = _clause_bounds(lower, start, end)
            if not is_year and family in (*_LENS_FAMILIES, "untagged") and re.search(r"\bratio\b", lower[lo:hi]):
                # "the asset-to-net-income ratio of 12.6%" is a ratio of lens metrics,
                # not a level of either — and not one this dictionary can name.
                family, name, label = "derived", "unnamed_ratio", "Unnamed ratio"
            elif not is_year and raw.endswith("%") and family in (*_LENS_FAMILIES, "market"):
                # "revenue up 16%" is a change in revenue, not revenue.
                family, name, label = "percent_change", f"{name}_change", f"{label} change"
            elif not is_year and raw.endswith("%") and family == "untagged" and _CHANGE_WORDS.search(lower):
                family, name, label = "percent_change", "untagged_change", "Unrecognised % change"
            period = UNKNOWN if is_year else _nearest_period(periods, start, end)
            facts.append(CanonicalFact(
                metric=name, label=label, family=family, value=value, raw=raw, period=period,
                sentence=conclusion[sent_start:sent_start + len(sentence)].strip(),
                span=(sent_start + start, sent_start + end),
            ))
    return facts


# ── Comparison ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MetricComparison:
    metric: str
    label: str
    family: Family
    status: Status
    value_a: float | None
    value_b: float | None
    raw_a: str | None
    raw_b: str | None
    period_a: str | None
    period_b: str | None
    variance_pct: float | None
    note: str | None = None

    @property
    def flags(self) -> bool:
        return self.status in FLAGGING_STATUSES

    def to_dict(self) -> dict:
        d = asdict(self)
        d["flags"] = self.flags
        return d


def _within(family: str, a: float, b: float) -> bool:
    kind, amount = TOLERANCE.get(family, ("rel", 0.01))
    return abs(a - b) <= amount + 1e-12 if kind == "abs" else close_enough(a, b, amount)


def _variance_pct(a: float, b: float) -> float | None:
    denom = max(abs(a), abs(b))
    return round(abs(a - b) / denom * 100, 2) if denom else None


_SEVERITY: dict[str, int] = {
    "MISMATCH": 0, "UNCORROBORATED": 1, "DERIVED_WRONG": 2, "DIFFERENT_PERIODS": 3,
    "UNVERIFIABLE_PERIOD": 4, "CITED_BY_ONE": 5, "ONE_SIDED": 5, "DERIVED_OK": 6, "MATCH": 7,
}


def _row(metric: str, label: str, family: Family, status: Status, a: CanonicalFact | None,
         b: CanonicalFact | None, note: str | None = None) -> MetricComparison:
    both = a is not None and b is not None
    return MetricComparison(
        metric=metric, label=label, family=family, status=status,
        value_a=a.value if a else None, value_b=b.value if b else None,
        raw_a=a.raw if a else None, raw_b=b.raw if b else None,
        period_a=a.period.label() if a else None, period_b=b.period.label() if b else None,
        variance_pct=_variance_pct(a.value, b.value) if both and status in ("MATCH", "MISMATCH") else None,
        note=note,
    )


def _pair(metric: str, label: str, family: Family,
          a_facts: list[CanonicalFact], b_facts: list[CanonicalFact]) -> MetricComparison:
    """One row for a metric both agents cite: the closest compatible pairing decides."""
    a_known = any(f.period.known for f in a_facts)
    b_known = any(f.period.known for f in b_facts)
    if not a_known and not b_known:
        pairs = [(a, b) for a in a_facts for b in b_facts]
        # A year is its own period; the shared-filing-period caveat is about financial figures.
        note = None if family == "year" else \
            "Neither agent stated a period; compared as the run's shared filing period."
    else:
        pairs = [(a, b) for a in a_facts for b in b_facts if a.period.known and b.period.known
                 and a.period.compatible(b.period)]
        note = None
    if pairs:
        a, b = min(pairs, key=lambda p: abs(p[0].value - p[1].value) / (max(abs(p[0].value), abs(p[1].value)) or 1))
        status: Status = "MATCH" if _within(family, a.value, b.value) else "MISMATCH"
        return _row(metric, label, family, status, a, b, note)
    a, b = a_facts[0], b_facts[0]
    if a_known != b_known:
        # Equal values with the period stated on one side only are the same figure,
        # said once with its period. Unequal ones can't be told apart from a
        # period difference, so they are not called a mismatch.
        agreeing = [(x, y) for x in a_facts for y in b_facts if _within(family, x.value, y.value)]
        if agreeing:
            x, y = agreeing[0]
            stated = x.period.label() or y.period.label()
            return _row(metric, label, family, "MATCH", x, y,
                        f"Values agree; only one agent stated the period ({stated}).")
        return _row(metric, label, family, "UNVERIFIABLE_PERIOD", a, b,
                    "Only one agent stated a period, so the values can't be matched.")
    return _row(metric, label, family, "DIFFERENT_PERIODS", a, b)


# Derived metric → (numerator, denominator). The agent's figure may be stated as a
# fraction (0.38) or a percentage (38.1%); both are accepted.
DERIVATIONS: dict[str, tuple[str, str]] = {
    "net_margin":       ("net_income", "revenue"),
    "operating_margin": ("operating_income", "revenue"),
    "gross_margin":     ("gross_profit", "revenue"),
    "return_on_assets": ("net_income", "total_assets"),
    "return_on_equity": ("net_income", "equity"),
    "asset_turnover":   ("revenue", "total_assets"),
    "debt_to_equity":   ("total_liabilities", "equity"),
    "current_ratio":    ("current_assets", "current_liabilities"),
}
# Looser than TOLERANCE["derived"]: an agent rounds both inputs before dividing
# ("assets of $922B, net income of $175B → ROA 18.85%" against 18.96% unrounded).
_DERIVATION_TOLERANCE = 0.02


def _derivation_row(fact: CanonicalFact, side: str, sources: list[CanonicalFact]) -> MetricComparison | None:
    """DERIVED_OK / DERIVED_WRONG if `fact` can be recomputed from `sources`, else None."""
    parts = DERIVATIONS.get(fact.metric)
    if not parts:
        return None
    num = next((f.value for f in sources if f.metric == parts[0]), None)
    den = next((f.value for f in sources if f.metric == parts[1]), None)
    if num is None or not den:
        return None
    expected = num / den
    ok = any(close_enough(fact.value, v, _DERIVATION_TOLERANCE) for v in (expected, expected * 100))
    # Show the recomputed value in whichever form the agent used (38.1% vs 0.38).
    as_pct = fact.raw.endswith("%") or abs(fact.value - expected * 100) < abs(fact.value - expected)
    shown = f"{expected * 100:.4g}%" if as_pct else f"{expected:.4g}"
    note = (f"Recomputed from agent {side.upper()}'s own figures and inputs: "
            f"{_BY_NAME[parts[0]].label} / {_BY_NAME[parts[1]].label} = {shown}.")
    a, b = (fact, None) if side == "a" else (None, fact)
    return _row(fact.metric, fact.label, fact.family, "DERIVED_OK" if ok else "DERIVED_WRONG", a, b, note)


def compare_facts(
    a_facts: list[CanonicalFact],
    b_facts: list[CanonicalFact],
    a_inputs: list[CanonicalFact] | None = None,
    b_inputs: list[CanonicalFact] | None = None,
) -> list[MetricComparison]:
    """
    Pair the two agents' facts per metric. a_inputs / b_inputs are facts extracted
    from each agent's *context* (what it was handed); they are used to recompute a
    one-sided derived ratio and to tell "only one agent was given this" from "both
    were given it, one cited it" — never compared across agents as values.
    """
    rows: list[MetricComparison] = []
    sources = {"a": a_facts + list(a_inputs or []), "b": b_facts + list(b_inputs or [])}
    given_to_both = {f.metric for f in a_inputs or []} & {f.metric for f in b_inputs or []}

    def group(facts):
        out: dict[str, list[CanonicalFact]] = {}
        for f in facts:
            if f.family != "untagged":
                out.setdefault(f.metric, []).append(f)
        return out

    ga, gb = group(a_facts), group(b_facts)

    # An agent that writes "diluted EPS of $4.27 … EPS $4.27" has stated one figure,
    # not two: an unspecified EPS equal to a specific one on the same side merges.
    for g in (ga, gb):
        if "eps" in g:
            specific = [f for m in ("eps_diluted", "eps_basic") for f in g.get(m, [])]
            g["eps"] = [f for f in g["eps"] if not any(_within("per_share", f.value, s.value) for s in specific)]
            if not g["eps"]:
                del g["eps"]

    # "EPS" with basic/diluted unstated pairs with whichever specific EPS the other
    # side gives (the closer value), rather than being a separate, one-sided metric.
    for this, other in ((ga, gb), (gb, ga)):
        if "eps" in this and "eps" not in other:
            specific = [m for m in ("eps_diluted", "eps_basic") if m in other]
            if specific:
                target = min(specific, key=lambda m: min(
                    abs(x.value - y.value) for x in this["eps"] for y in other[m]))
                this.setdefault(target, []).extend(this.pop("eps"))

    for metric in sorted(set(ga) | set(gb)):
        a_list, b_list = ga.get(metric, []), gb.get(metric, [])
        sample = (a_list or b_list)[0]
        # A rehomed unspecified-EPS fact carries its old label; the row takes the metric's.
        label = _BY_NAME[metric].label if metric in _BY_NAME else sample.label
        if a_list and b_list:
            rows.append(_pair(metric, label, sample.family, a_list, b_list))
        else:
            side = "a" if a_list else "b"
            derived = _derivation_row(sample, side, sources[side]) if sample.family == "derived" else None
            if derived is not None:
                rows.append(derived)
                continue
            if sample.family in _LENS_FAMILIES:
                status: Status = "CITED_BY_ONE" if metric in given_to_both else "ONE_SIDED"
            else:
                status = "UNCORROBORATED"
            rows.append(_row(metric, sample.label, sample.family, status,
                             a_list[0] if a_list else None, b_list[0] if b_list else None))

    # Unrecognised figures keep concept_aware's presence/absence rule, by value.
    ua = [f for f in a_facts if f.family == "untagged"]
    ub = [f for f in b_facts if f.family == "untagged"]
    matched_b: set[int] = set()
    for fa in ua:
        hit = next((i for i, fb in enumerate(ub) if i not in matched_b and _within("untagged", fa.value, fb.value)), None)
        if hit is None:
            rows.append(_row("untagged", fa.label, "untagged", "UNCORROBORATED", fa, None))
        else:
            matched_b.add(hit)
            rows.append(_row("untagged", fa.label, "untagged", "MATCH", fa, ub[hit]))
    for i, fb in enumerate(ub):
        if i not in matched_b:
            rows.append(_row("untagged", fb.label, "untagged", "UNCORROBORATED", None, fb))

    return sorted(rows, key=lambda r: (_SEVERITY[r.status], r.label))


def context_facts(context: str | None) -> list[CanonicalFact]:
    """Facts in an agent's context, one line at a time (lens contexts are `Concept: value …` lines)."""
    return [f for line in (context or "").splitlines() for f in extract_facts(line)]


def contradiction_flag_canonical(
    a_conclusion: str,
    b_conclusion: str,
    *,
    include_years: bool = False,
    context_a: str | None = None,
    context_b: str | None = None,
) -> tuple[bool, list[str], list[MetricComparison]]:
    """(flag, the raw figures that drove it, every comparison row)."""
    rows = compare_facts(
        extract_facts(a_conclusion, include_years=include_years),
        extract_facts(b_conclusion, include_years=include_years),
        context_facts(context_a),
        context_facts(context_b),
    )
    divergent = sorted({raw for r in rows if r.flags for raw in (r.raw_a, r.raw_b) if raw})
    return any(r.flags for r in rows), divergent, rows
