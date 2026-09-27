"""
B4 — the structured <assessment> block: an agent's grade, direction and stated
assumptions, as data rather than prose.

Why this exists
    A conclusion is free text; two agents' free-text views can't be compared,
    counted or gated on (B5 needs "Bull says BBB/buy on 8% growth, Bear says
    BB/sell on 3%"). Directive v1.6.0 asks for an optional third block after
    </conclusion>:

        <assessment>{"grade": ..., "direction": ..., "assumptions": {...},
                     "key_metrics": [...], "key_points": [...]}</assessment>

What this module guarantees
    - Closed vocabulary. A grade, direction, assumption key or metric outside it is
      a recorded issue, never coerced into the nearest valid value (P3): "A-" is not
      turned into "A", "Strong Buy" is not turned into "buy". Only valid fields are
      kept in `assessment`; the block's raw text is kept separately, always.
    - Strict JSON. Invalid JSON is never repaired; the issue says where it broke.
    - Never a structural failure. A missing, unclosed or invalid block is recorded
      with a status and never fails ADR-07's contract or halts a run — the
      two-block contract is unchanged; this block is extra.

    A grade is the model's judgment about the subject, not a verified fact (P8).
    It is internal-tier (SEC-01), like thought_log: investors don't see it until a
    human has ruled on it (B5/U8).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from core.numeric import QUANTITATIVE_RE, close_enough, normalize_number


ASSESSMENT_FROM = (1, 6, 0)  # the first directive version that asks for the block

GRADES = ("AAA", "AA", "A", "BBB", "BB", "B", "CCC")
DIRECTIONS = ("buy", "hold", "sell")
MARGIN_TRENDS = ("expanding", "stable", "contracting")
# Assumption key -> what a valid value is.
ASSUMPTIONS: dict[str, str] = {
    "revenue_growth_pct": "number",   # expected annual revenue growth, percent
    "margin_trend": "margin_trend",   # expanding | stable | contracting
    "horizon_months": "number",       # the period the view is about
}
# The canonical metric names of validation/facts.py's METRICS (financial families only).
# Listed here, not imported: core/ imports nothing internal (tests/test_layering.py),
# and pipeline/ — which validates this block — may only import core/.
# tests/test_assessment.py fails if this list and METRICS ever drift apart.
KEY_METRICS = (
    "revenue", "gross_profit", "operating_income", "net_income", "eps_diluted", "eps_basic", "eps",
    "operating_cash_flow", "capex", "free_cash_flow", "current_assets", "total_assets",
    "current_liabilities", "total_liabilities", "equity", "gross_margin", "operating_margin",
    "net_margin", "debt_to_equity", "return_on_equity", "return_on_assets", "current_ratio",
    "asset_turnover", "market_cap", "share_price",
)
MAX_KEY_POINTS = 3
MAX_POINT_CHARS = 240

# valid: every field usable · partial: some fields usable, issues listed · invalid_fields:
# parsed, nothing usable · invalid_json · unclosed · empty · absent (none given) ·
# abstained: the extraction said the answer states no view (an honest answer, kept as
# such) · extraction_failed: the extraction call itself didn't return.
Status = Literal["valid", "partial", "invalid_fields", "invalid_json", "unclosed", "absent", "empty",
                 "abstained", "extraction_failed"]


@dataclass
class ParsedAssessment:
    status: Status
    assessment: dict[str, Any] | None
    issues: list[str] = field(default_factory=list)
    raw: str | None = None


def expects_assessment(directive_version: str | None) -> bool:
    """True for directives that ask for the block (v1.6.0 and later). "corrective" and ≤v1.5.x don't."""
    if not directive_version or not directive_version.startswith("v"):
        return False
    try:
        parts = tuple(int(p) for p in directive_version[1:].split("."))
    except ValueError:
        return False
    return parts >= ASSESSMENT_FROM


# ── Option 1: a separate extraction call (the human's choice, 2026-09-26) ───────
# Directive v1.6.0 asked the agent for the block inside its own answer and cut
# format compliance to 2 of 10 (logs/RUN_LOG.md). Instead the agent answers under
# the unchanged two-block directive, and one more call to the same model reads that
# finished answer and returns only the JSON. It adds no judgment of its own: it
# reports the view the answer states, or abstains. ADR-07 is untouched.

# v1 -> v2 (2026-09-26): live, v1 reported past growth ("revenue up 16% year over
# year", NVDA's 106%) as revenue_growth_pct, which B5 then read as a difference in
# assumptions. v2 says a past rate is not an assumption; ground_in enforces it.
EXTRACTION_PROMPT_VERSION = "assess-extract-v2"

EXTRACTION_SYSTEM = (
    "You read one analyst's finished answer about a company and report the view it states, as one JSON "
    "object and nothing else — no prose, no code fence.\n"
    "Keys:\n"
    "- \"grade\": one of AAA, AA, A, BBB, BB, B, CCC — how financially strong the answer judges the company\n"
    "- \"direction\": one of buy, hold, sell — what the answer's view implies\n"
    "- \"assumptions\": optional; only what the answer itself EXPECTS going forward: \"revenue_growth_pct\" "
    "(number), \"margin_trend\" (expanding, stable or contracting), \"horizon_months\" (number). A growth rate "
    "that already happened (\"revenue was up 16% year over year\") is a past result, not an assumption: leave it out.\n"
    "- \"key_metrics\": the metric names the view rests on, from: revenue, net_income, eps_diluted, eps_basic, "
    "operating_income, total_assets, gross_profit, operating_cash_flow, free_cash_flow\n"
    "- \"key_points\": at most 3 short sentences taken from the answer; copy figures exactly as the answer writes them\n"
    "Report only what the answer says. Do not add facts, figures or opinions of your own. If the answer states no "
    "view of the company's strength or prospects, reply exactly {\"abstain\": true, \"reason\": \"<why, briefly>\"}."
)


def extraction_user_prompt(subject: str, conclusion: str, thought_log: str | None) -> str:
    return (f"Company: {subject}\n\nThe analyst's reasoning:\n{thought_log or '(none recorded)'}\n\n"
            f"The analyst's answer:\n{conclusion}\n\nReply with the JSON object only.")


# A growth assumption is about the future. Kept only if a sentence of the agent's own
# that contains the number also looks forward; a past result is not an assumption.
_FORWARD = re.compile(
    r"\b(expect\w*|forecast\w*|guidance|guid(?:e|ed|ing)|project\w*|outlook|anticipat\w*|will|going forward|"
    r"next (?:year|quarter|fiscal)|estimate[sd]?|target\w*|assum\w*|fiscal 20\d\d)\b", re.IGNORECASE)
_SENTENCES = re.compile(r"[^.!?\n]+[.!?]?")


def _stated_as_forward(value: float, texts: tuple[str | None, ...]) -> bool:
    for t in texts:
        for sentence in _SENTENCES.findall(t or ""):
            nums = [normalize_number(m.group(0)) for m in QUANTITATIVE_RE.finditer(sentence)]
            nums += [float(n) for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", sentence)]
            if any(n is not None and abs(n - value) < 1e-9 for n in nums) and _FORWARD.search(sentence):
                return True
    return False


def _values(text: str) -> list[float]:
    return [v for m in QUANTITATIVE_RE.finditer(text or "") if (v := normalize_number(m.group(0))) is not None]


def _backed(value: float, sources: list[float]) -> bool:
    return any(close_enough(value, s, 0.005) or abs(value - s) < 1e-9 for s in sources)


def ground_in(parsed: ParsedAssessment, *texts: str | None) -> ParsedAssessment:
    """
    Drop anything the extraction added that the agent never wrote: a key point
    quoting a figure absent from the agent's own answer and inputs, or an assumption
    value it never stated. Each drop is an issue, with the figure. (Seen live under
    v1.6.0: key points that misquoted the context by 10x — nothing checked them.)
    """
    if not parsed.assessment:
        return parsed
    sources = [v for t in texts for v in _values(t or "")]
    bare = [float(n) for t in texts for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", t or "")]
    a = dict(parsed.assessment)
    issues = list(parsed.issues)
    points = []
    for p in a.get("key_points", []):
        missing = [m.group(0) for m in QUANTITATIVE_RE.finditer(p)
                   if (v := normalize_number(m.group(0))) is not None and not _backed(v, sources)]
        if missing:
            issues.append(f"Dropped a key point quoting {', '.join(missing)}, which isn't in the agent's answer or inputs.")
        else:
            points.append(p)
    if points:
        a["key_points"] = points
    else:
        a.pop("key_points", None)
    assumptions = dict(a.get("assumptions", {}))
    for key in ("revenue_growth_pct", "horizon_months"):
        if key in assumptions and not _backed(float(assumptions[key]), sources + bare):
            issues.append(f"Dropped assumption {key}={assumptions[key]}: the agent never stated it.")
            del assumptions[key]
    growth = assumptions.get("revenue_growth_pct")
    if growth is not None and not _stated_as_forward(float(growth), texts):
        issues.append(f"Dropped assumption revenue_growth_pct={growth}: the agent states it as a past result, "
                      "not as an expectation.")
        del assumptions["revenue_growth_pct"]
    if assumptions:
        a["assumptions"] = assumptions
    else:
        a.pop("assumptions", None)
    status: Status = parsed.status if issues == parsed.issues else ("partial" if a else "invalid_fields")
    return ParsedAssessment(status, a or None, issues, parsed.raw)


def _json_object(reply: str) -> tuple[str, bool]:
    """The reply's JSON object text. A model that wraps it in a code fence or a sentence
    still gets its object read — but the wrapping is reported, not hidden."""
    stripped = reply.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped, False
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start:end + 1], True
    return stripped, False


def extract_assessment(call, subject: str, conclusion: str, thought_log: str | None,
                       context: str | None) -> ParsedAssessment:
    """
    Run the extraction call and validate its reply. Never raises: a call that fails
    is recorded as extraction_failed with the error, and the run goes on.
    """
    try:
        reply = call(EXTRACTION_SYSTEM, extraction_user_prompt(subject, conclusion, thought_log))
    except Exception as exc:  # the model or connection failed; the answer itself stands
        return ParsedAssessment("extraction_failed", None, [f"The extraction call failed: {type(exc).__name__}: {exc}"], None)
    body, wrapped = _json_object(reply or "")
    parsed = parse_assessment(body, closed=True)
    parsed.raw = reply
    if wrapped:
        parsed.issues.insert(0, "The reply had text around its JSON object; only the object was read.")
        if parsed.status == "valid":
            parsed.status = "partial"  # "valid" means nothing to report
    return ground_in(parsed, conclusion, thought_log, context)


def parse_assessment(text: str | None, *, closed: bool | None) -> ParsedAssessment:
    """
    Validate the block's inner text. `closed` is whether </assessment> was found
    (None: the parser wasn't looking, i.e. the directive didn't ask for one).
    """
    if text is None:
        return ParsedAssessment("absent", None, ["No <assessment> block was given."])
    raw = text
    body = text.strip()
    if not closed:
        issues = ["Found an unclosed <assessment> tag: nothing after it was read as the block."]
        if not body:
            return ParsedAssessment("unclosed", None, issues, raw)
    else:
        issues = []
    if not body:
        return ParsedAssessment("empty", None, ["The <assessment> block is empty."], raw)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        issues.append(f"JSON didn't parse at line {exc.lineno}, column {exc.colno}: {exc.msg}.")
        return ParsedAssessment("unclosed" if not closed else "invalid_json", None, issues, raw)
    if not isinstance(data, dict):
        issues.append("The block must be a JSON object ({...}).")
        return ParsedAssessment("invalid_fields", None, issues, raw)
    if data.get("abstain") is True:
        reason = data.get("reason") if isinstance(data.get("reason"), str) else None
        return ParsedAssessment("abstained", None, [reason or "The answer states no view to grade."], raw)

    out: dict[str, Any] = {}
    grade = data.get("grade")
    if grade in GRADES:
        out["grade"] = grade
    else:
        issues.append(f"grade {grade!r} isn't one of {', '.join(GRADES)}." if grade is not None else "No grade given.")
    direction = data.get("direction")
    if direction in DIRECTIONS:
        out["direction"] = direction
    else:
        issues.append(f"direction {direction!r} isn't one of {', '.join(DIRECTIONS)}." if direction is not None
                      else "No direction given.")

    assumptions = data.get("assumptions")
    if assumptions is not None:
        if not isinstance(assumptions, dict):
            issues.append("assumptions must be an object.")
        else:
            kept: dict[str, Any] = {}
            for key, value in assumptions.items():
                kind = ASSUMPTIONS.get(key)
                if kind is None:
                    issues.append(f"assumption {key!r} isn't one of {', '.join(ASSUMPTIONS)}.")
                elif kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
                    issues.append(f"assumption {key} must be a number, got {value!r}.")
                elif kind == "margin_trend" and value not in MARGIN_TRENDS:
                    issues.append(f"assumption margin_trend {value!r} isn't one of {', '.join(MARGIN_TRENDS)}.")
                else:
                    kept[key] = value
            if kept:
                out["assumptions"] = kept

    metrics = data.get("key_metrics")
    if metrics is not None:
        if not isinstance(metrics, list):
            issues.append("key_metrics must be a list.")
        else:
            good = [m for m in metrics if m in KEY_METRICS]
            bad = [m for m in metrics if m not in KEY_METRICS]
            if bad:
                issues.append(f"key_metrics not recognised: {', '.join(map(repr, bad))}.")
            if good:
                out["key_metrics"] = good

    points = data.get("key_points")
    if points is not None:
        if not isinstance(points, list) or not all(isinstance(p, str) for p in points):
            issues.append("key_points must be a list of short strings.")
        else:
            kept_points = [p.strip() for p in points if p.strip()]
            if len(kept_points) > MAX_KEY_POINTS:
                issues.append(f"key_points has {len(kept_points)} items; only the first {MAX_KEY_POINTS} are kept.")
            long = [p for p in kept_points[:MAX_KEY_POINTS] if len(p) > MAX_POINT_CHARS]
            if long:
                issues.append(f"{len(long)} key point(s) longer than {MAX_POINT_CHARS} characters were dropped.")
            kept_points = [p for p in kept_points[:MAX_KEY_POINTS] if len(p) <= MAX_POINT_CHARS]
            if kept_points:
                out["key_points"] = kept_points

    unknown = sorted(set(data) - {"grade", "direction", "assumptions", "key_metrics", "key_points", "abstain", "reason"})
    if unknown:
        issues.append(f"Ignored unknown field(s): {', '.join(unknown)}.")

    if not closed:
        return ParsedAssessment("unclosed", out or None, issues, raw)
    status: Status = "valid" if not issues else "partial" if out else "invalid_fields"
    return ParsedAssessment(status, out or None, issues, raw)
