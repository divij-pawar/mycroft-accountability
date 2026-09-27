"""
B3 — accounting checks (Tier 2 of the audit-layer roadmap).

B1 compares two agents with each other. This module asks a different question of
each agent on its own, and of the filing it was handed: do the figures add up?

Three passes, each producing CheckResults:

  1. Internal consistency (per agent) — the agent's own stated figures against
     accounting relationships (RULES below). "Basic EPS $1.57, diluted EPS $1.60"
     can't both be right, whatever the other agent said.
  2. Claim vs source (per agent) — each figure the agent cites for a metric it was
     *given* (its lens facts from the one EDGAR fetch, datasources.edgar.Fact) against
     that given value. An agent misquoting its own input is the most direct error
     there is, and the corpus's history is full of it being caught only by a human
     reading the thought log (web/self_report.py's fabrication-not-caught).
  3. Source sanity — the same RULES over the filing itself, straight from the
     companyfacts payload already fetched (no new HTTP call). This one reports,
     it never gates: a filing that doesn't add up means the data selection needs a
     look, not that either agent is wrong.

Hard vs heuristic
    Only definitional relationships are hard: FCF = OCF − CapEx, basic EPS ≥ diluted
    EPS, assets = liabilities + equity, and "a figure you were given matches what
    you were given". "Net income ≤ operating income" is NOT an identity — a one-off
    gain or a tax benefit legitimately breaks it (the approved plan says so) — so it
    and its siblings are heuristics: shown as "unusual, worth a look", never gating.

Periods
    A rule runs only on figures that describe one period. Agents rarely state a
    period next to each figure; figures with no stated period are taken as the
    run's shared filing period (validation/facts.py does the same). If stated
    periods disagree, the rule is SKIPPED with that reason, never failed.

What is gated (validation/gate.py)
    A failed HARD check in pass 1 or 2 is a gate item, exactly like a MISMATCH row:
    the run is AWAITING_DECISION until a human rules on it. Heuristic warnings and
    every source-sanity result are shown and never gate.

Nothing here decides what the right figure is. A check says what it computed; the
human decides.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Literal

from core.numeric import close_enough
from validation.facts import (
    METRICS,
    TOLERANCE,
    CanonicalFact,
    Period,
    extract_facts,
)

CHECKS_POLICY = "b3-v1"

Kind = Literal["hard", "heuristic"]
Outcome = Literal["pass", "fail", "skipped"]
Scope = Literal["agent_a", "agent_b", "source"]

_LABEL = {m.name: m.label for m in METRICS}
_LABEL["liabilities_and_equity"] = "Liabilities and equity"
_FAMILY = {m.name: m.family for m in METRICS}


# ── Rules ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Rule:
    id: str
    kind: Kind
    metrics: tuple[str, ...]
    plain: str                      # what the rule says, in words
    rationale: str                  # why it is hard / only a heuristic
    check: Callable[[dict[str, float]], tuple[bool, str]]   # (holds, the math shown)


def _money(v: float) -> str:
    a = abs(v)
    sign = "−" if v < 0 else ""
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{sign}${a / div:.4g}{suf}"
    return f"{sign}${a:.4g}"


def _eps(v: float) -> str:
    return f"{'−' if v < 0 else ''}${abs(v):.2f}"


def _basic_ge_diluted(v: dict[str, float]) -> tuple[bool, str]:
    basic, diluted = v["eps_basic"], v["eps_diluted"]
    # With a loss, dilutive securities are anti-dilutive and excluded, so the two are equal.
    ok = diluted <= basic + 0.005 if basic >= 0 else abs(diluted - basic) <= 0.005
    return ok, f"basic {_eps(basic)} vs diluted {_eps(diluted)}"


def _fcf(v: dict[str, float]) -> tuple[bool, str]:
    ocf, capex, fcf = v["operating_cash_flow"], abs(v["capex"]), v["free_cash_flow"]
    expected = ocf - capex
    return close_enough(fcf, expected, 0.01), \
        f"{_money(ocf)} − {_money(capex)} = {_money(expected)}; stated {_money(fcf)}"


def _balance(v: dict[str, float]) -> tuple[bool, str]:
    assets, total = v["total_assets"], v["total_liabilities"] + v["equity"]
    # 2%: a stated "shareholders' equity" often excludes noncontrolling interest.
    return close_enough(assets, total, 0.02), \
        f"{_money(v['total_liabilities'])} + {_money(v['equity'])} = {_money(total)}; assets {_money(assets)}"


def _balance_filed(v: dict[str, float]) -> tuple[bool, str]:
    assets, total = v["total_assets"], v["liabilities_and_equity"]
    return close_enough(assets, total, 0.001), f"assets {_money(assets)} vs liabilities and equity {_money(total)}"


def _le(small: str, big: str) -> Callable[[dict[str, float]], tuple[bool, str]]:
    def check(v: dict[str, float]) -> tuple[bool, str]:
        return v[small] <= v[big] * (1 + 1e-9), \
            f"{_LABEL[small].lower()} {_money(v[small])} vs {_LABEL[big].lower()} {_money(v[big])}"
    return check


RULES: tuple[Rule, ...] = (
    Rule("eps_basic_ge_diluted", "hard", ("eps_basic", "eps_diluted"),
         "Diluted EPS can't be higher than basic EPS",
         "Dilution only adds shares, so it can only lower (or, with a loss, leave unchanged) per-share earnings.",
         _basic_ge_diluted),
    Rule("fcf_identity", "hard", ("operating_cash_flow", "capex", "free_cash_flow"),
         "Free cash flow is operating cash flow minus capital expenditures",
         "That is the definition of free cash flow.",
         _fcf),
    Rule("balance_sheet", "hard", ("total_assets", "total_liabilities", "equity"),
         "Assets equal liabilities plus equity",
         "The balance-sheet identity. Checked to 2%, because a stated equity figure often excludes noncontrolling interest.",
         _balance),
    Rule("net_le_operating", "heuristic", ("net_income", "operating_income"),
         "Net income is usually no more than operating income",
         "Not an identity: a one-off gain or a tax benefit can legitimately push net income above operating income.",
         _le("net_income", "operating_income")),
    Rule("operating_le_gross", "heuristic", ("operating_income", "gross_profit"),
         "Operating income is usually no more than gross profit",
         "Operating expenses are normally positive; an operating gain or reclassification can break it.",
         _le("operating_income", "gross_profit")),
    Rule("gross_le_revenue", "heuristic", ("gross_profit", "revenue"),
         "Gross profit is usually no more than revenue",
         "Cost of revenue is normally positive.",
         _le("gross_profit", "revenue")),
)

# Source sanity uses the filed total rather than adding two figures a filer may
# define differently; FCF isn't a filed concept at all, so it isn't checked there.
SOURCE_RULES: tuple[Rule, ...] = (
    Rule("balance_sheet_filed", "hard", ("total_assets", "liabilities_and_equity"),
         "Filed assets equal filed liabilities and equity",
         "The balance-sheet identity, on the filer's own totals.",
         _balance_filed),
    *(r for r in RULES if r.id not in ("fcf_identity", "balance_sheet")),
)

# us-gaap tags per metric, for source sanity. Revenues has the same fallbacks as
# datasources.edgar.CONCEPT_TAGS (AAPL stopped filing us-gaap:Revenues in FY2018).
SOURCE_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "eps_basic": ("EarningsPerShareBasic",),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "total_assets": ("Assets",),
    "liabilities_and_equity": ("LiabilitiesAndStockholdersEquity",),
}

# A lens concept -> the canonical metric its figure is (claim-vs-source).
CONCEPT_METRIC: dict[str, str] = {tag: m.name for m in METRICS for tag in m.xbrl_tags}


# ── Results ────────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    key: str                  # stable id; a gate item key when it gates
    rule: str                 # rule id, or "matches_source"
    kind: Kind
    scope: Scope
    outcome: Outcome
    plain: str
    math: str | None
    reason: str | None = None           # why skipped, or the rationale for a heuristic
    metrics: list[str] = field(default_factory=list)

    @property
    def gates(self) -> bool:
        """A failed hard check about an agent's own figures opens the decision gate."""
        return self.kind == "hard" and self.outcome == "fail" and self.scope != "source"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gates"] = self.gates
        d["who"] = _who(self.scope)
        return d


def _who(scope: Scope) -> str:
    return {"agent_a": "Agent A", "agent_b": "Agent B", "source": "The filing"}[scope]


# ── Pass 1: an agent's own figures ─────────────────────────────────────────────

def _compatible(periods: Iterable[Period]) -> bool:
    known = [p for p in periods if p.known]
    return all(a.compatible(b) for a, b in itertools.combinations(known, 2))


def _agent_rules(facts: list[CanonicalFact], scope: Scope) -> list[CheckResult]:
    by_metric: dict[str, list[CanonicalFact]] = {}
    for f in facts:
        by_metric.setdefault(f.metric, []).append(f)
    out: list[CheckResult] = []
    for rule in RULES:
        if not all(m in by_metric for m in rule.metrics):
            continue  # the agent didn't state these figures: nothing to check, nothing to show
        combos = [c for c in itertools.product(*(by_metric[m] for m in rule.metrics))
                  if _compatible(f.period for f in c)]
        key = f"check:{scope}:{rule.id}"
        plain = rule.plain
        if not combos:
            out.append(CheckResult(key, rule.id, rule.kind, scope, "skipped", plain, None,
                                   "Not checked: the periods these figures are stated for don't line up.",
                                   list(rule.metrics)))
            continue
        # Any consistent reading passes; only if every reading fails is it a failure.
        results = [rule.check({m: f.value for m, f in zip(rule.metrics, c)}) for c in combos]
        holds, math = next(((h, m) for h, m in results if h), results[0])
        out.append(CheckResult(key, rule.id, rule.kind, scope, "pass" if holds else "fail", plain, math,
                               None if holds or rule.kind == "hard" else rule.rationale, list(rule.metrics)))
    return out


# ── Pass 2: an agent's figures against what it was given ───────────────────────

def _given_period(fact: dict[str, Any]) -> Period | None:
    """The given fact's period in facts.py's terms; None for a point-in-time figure."""
    kind, fy, fp = fact.get("period_kind"), fact.get("fy"), fact.get("fp") or ""
    if kind == "quarter" and re.fullmatch(r"Q[1-4]", fp):
        return Period("quarter", fy, int(fp[1]))
    if kind == "annual":
        return Period("annual", fy)
    if kind == "ytd":
        return Period("ytd", fy)
    return None


def _precision(raw: str, value: float) -> float:
    """One unit of the last digit the agent wrote, in the figure's own scale ($94.9B → $0.1B)."""
    m = re.search(r"\d[\d,]*(?:\.(\d+))?", raw)
    if not m:
        return 0.0
    mantissa = float(m.group(0).replace(",", ""))
    decimals = len(m.group(1) or "")
    scale = value / mantissa if mantissa else 1.0
    return abs(10 ** -decimals * scale)


def _agrees_with_source(f: CanonicalFact, given: float) -> bool:
    kind, amount = TOLERANCE.get(f.family, ("rel", 0.005))
    within = abs(f.value - given) <= amount + 1e-12 if kind == "abs" else close_enough(f.value, given, amount)
    # Rounding or truncating to the digits the agent wrote is not misquoting.
    return within or abs(f.value - given) <= _precision(f.raw, f.value) + 1e-9


def _fmt(metric: str, v: float) -> str:
    return _eps(v) if _FAMILY.get(metric) == "per_share" else _money(v)


def _claims_vs_source(facts: list[CanonicalFact], given: list[dict[str, Any]], scope: Scope) -> list[CheckResult]:
    out: list[CheckResult] = []
    for g in given:
        if g.get("missing") or g.get("value") is None:
            continue
        metric = CONCEPT_METRIC.get(g.get("concept", ""))
        cited = [f for f in facts if f.metric == metric] if metric else []
        if not cited:
            continue
        gp = _given_period(g)
        comparable = [f for f in cited if gp is None or not f.period.known or f.period.compatible(gp)]
        label = _LABEL.get(metric, metric)
        key = f"source:{scope}:{metric}"
        plain = f"{label} matches the filing value the agent was given"
        stated = f"{_fmt(metric, g['value'])} ({g.get('period_label') or 'period unknown'}"
        stated += f", {g['form']})" if g.get("form") else ")"
        if not comparable:
            out.append(CheckResult(key, "matches_source", "hard", scope, "skipped", plain,
                                   f"cited {', '.join(f.raw for f in cited)}; given {stated}",
                                   "Not checked: the agent states a different period than the one it was given.",
                                   [metric]))
            continue
        # One matching figure is enough: an agent often cites the prior period beside the current one.
        holds = any(_agrees_with_source(f, g["value"]) for f in comparable)
        shown = next((f for f in comparable if _agrees_with_source(f, g["value"])), comparable[0])
        others = "" if holds or len(comparable) == 1 else f" (also cited: {', '.join(f.raw for f in comparable[1:])})"
        out.append(CheckResult(key, "matches_source", "hard", scope, "pass" if holds else "fail", plain,
                               f"cited {shown.raw}{others}; given {stated}", None, [metric]))
    return out


# ── Pass 3: the filing itself ──────────────────────────────────────────────────

def _series(payload: dict, metric: str) -> dict[tuple[str | None, str], float]:
    """(start, end) -> value for every entry of the metric's tags; latest filing wins."""
    try:
        us_gaap = payload["facts"]["us-gaap"]
    except (KeyError, TypeError):
        return {}
    best: dict[tuple[str | None, str], tuple[str, float]] = {}
    for tag in SOURCE_TAGS[metric]:
        for entries in ((us_gaap.get(tag) or {}).get("units") or {}).values():
            for e in entries:
                if not isinstance(e.get("val"), (int, float)) or not e.get("end"):
                    continue
                k = (e.get("start"), e["end"])
                filed = e.get("filed") or ""
                if k not in best or filed >= best[k][0]:
                    best[k] = (filed, float(e["val"]))
    return {k: v for k, (_, v) in best.items()}


def _span_label(start: str | None, end: str) -> str:
    return f"as of {end}" if not start else f"{start} to {end}"


def _source_rules(payload: dict) -> list[CheckResult]:
    out: list[CheckResult] = []
    for rule in SOURCE_RULES:
        series = [_series(payload, m) for m in rule.metrics]
        if not all(series):
            continue  # the filer doesn't report one of these concepts
        common = set(series[0]).intersection(*series[1:])
        key = f"check:source:{rule.id}"
        if not common:
            out.append(CheckResult(key, rule.id, rule.kind, "source", "skipped", rule.plain, None,
                                   "Not checked: these figures were never filed for the same period.",
                                   list(rule.metrics)))
            continue
        # The latest period they share; for the same end date, the shortest span (the quarter).
        start, end = max(common, key=lambda k: (k[1], k[0] or ""))
        holds, math = rule.check({m: s[(start, end)] for m, s in zip(rule.metrics, series)})
        out.append(CheckResult(key, rule.id, rule.kind, "source", "pass" if holds else "fail", rule.plain,
                               f"{math} ({_span_label(start, end)})",
                               None if holds or rule.kind == "hard" else rule.rationale, list(rule.metrics)))
    return out


# ── Entry point ────────────────────────────────────────────────────────────────

def run_checks(
    a_conclusion: str | None,
    b_conclusion: str | None,
    *,
    given_facts: dict[str, list[dict[str, Any]]] | None = None,
    source_payload: dict | None = None,
) -> dict[str, Any]:
    """
    Every check for one compare run, as the payload's `structural_flags`. Given
    facts are datasources.edgar.Fact.to_dict() dicts per slot; the source payload is
    the companyfacts JSON both agents' facts came from. Either may be absent
    (generic-subject runs have neither) — then those passes simply don't run.
    """
    given_facts = given_facts or {}
    checks: list[CheckResult] = []
    for slot, conclusion in (("a", a_conclusion), ("b", b_conclusion)):
        if not conclusion:
            continue
        scope: Scope = "agent_a" if slot == "a" else "agent_b"
        facts = extract_facts(conclusion)
        checks += _agent_rules(facts, scope)
        checks += _claims_vs_source(facts, given_facts.get(slot) or [], scope)
    if source_payload:
        checks += _source_rules(source_payload)
    order = {"fail": 0, "skipped": 1, "pass": 2}
    checks.sort(key=lambda c: (order[c.outcome], c.kind != "hard", c.scope, c.key))
    return {
        "policy": CHECKS_POLICY,
        "checks": [c.to_dict() for c in checks],
        "gating": [c.key for c in checks if c.gates],
    }
