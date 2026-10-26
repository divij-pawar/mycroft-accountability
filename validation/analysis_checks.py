"""
Tier 1 grounding of an analysis: did the agent quote its data correctly?

Why this exists
    An analysis record lists the figures its signal rests on, each with the tool call it says
    the figure came from (core/analysis.py `cited_figures`). The tool calls are recorded with
    exactly what the agent was given. So whether a cited figure is real can be checked in code,
    with no model: find the call, find the number, compare. This module does that.

What it says, per figure
    GROUNDED        the value matches a number in the call it cites, at the precision it was written.
    MISMATCH        the call holds a number the figure's name points at, and it is a different one.
                    "rsi_14 is 58.2" when call 2 says RSI is 53.78. This is the one that is wrong.
    UNREFERENCED    the call doesn't exist, returned an error, or holds nothing the figure matches.
    USER_ASSERTED   the figure came from a user's document. Shown, never counted as grounded.
    JUDGMENT        a reason that rests on no figure. Counted in neither direction.

What it does not say
    That the signal is right, that the data is right (a feed can be wrong and consistent), or that
    a figure the agent did not cite is wrong. A figure matched by value alone, because its name
    pointed at nothing in the call, is marked `matched_by: "value"`. Names are matched by words, not
    understood: a parameter the data lacks (`sma_21` when the call has the 20 and 50 day means) is a
    MISMATCH, but a name that is merely a loose description of the right number is accepted.

How a value is compared
    At the precision the agent wrote it: 53.8 matches 53.7839 (one decimal, so half a tenth either
    way), 58.2 does not. A stated unit of thousand, million, billion or trillion scales the figure
    first, so "109.417 billion" matches 109417000000 and "109.417 million" does not. No unit means
    no scaling: a bare 109.4 doesn't match a revenue in dollars, which is how a unit slip is caught.

Pure functions of plain dicts shaped like an AnalysisRecord. This module never imports
financial_agent/ (PLAN.md D1; tests/test_layering.py).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

CHECKS_VERSION = "tier1-v1"

GROUNDED = "GROUNDED"
MISMATCH = "MISMATCH"
UNREFERENCED = "UNREFERENCED"
USER_ASSERTED = "USER_ASSERTED"
JUDGMENT = "JUDGMENT"

_SCALES = (("trillion", 1e12), ("billion", 1e9), ("million", 1e6), ("thousand", 1e3))
_SCALE_ABBREV = {"tn": 1e12, "t": 1e12, "bn": 1e9, "b": 1e9, "mn": 1e6, "mm": 1e6, "m": 1e6, "k": 1e3}

# Words that say nothing about which number is meant.
_GENERIC = {"value", "values", "index", "usd", "pct", "percent", "the", "of", "a", "an", "in", "for", "and", "is",
            "current", "latest", "recent", "last"}
# A figure's name in the agent's words, to the words the data uses for it.
_SYNONYMS = {
    "eps": {"earning", "share"},
    "sales": {"revenue"},
    "turnover": {"revenue"},
    "profit": {"income"},
    "price": {"close"},
    "rsi": {"rsi"},
}


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _tokens(text: Any) -> set[str]:
    """Lower-case words and numbers of `text`, camelCase split, plurals folded, generic words dropped."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(text))
    out: set[str] = set()
    for tok in re.findall(r"[a-z]+|\d+", spaced.lower()):
        tok = _stem(tok)
        if tok in _GENERIC:
            continue
        out.add(tok)
        out |= _SYNONYMS.get(tok, set())
    return out


def _decimals(value: float) -> int:
    exp = Decimal(repr(float(value))).as_tuple().exponent
    return max(0, -exp) if isinstance(exp, int) else 0


def _scale(unit: str | None) -> float:
    """The factor a stated unit puts on the figure, 1 when it names none."""
    if not unit:
        return 1.0
    text = unit.lower()
    for word, factor in _SCALES:
        if re.search(rf"\b{word}s?\b", text):
            return factor
    for abbrev, factor in _SCALE_ABBREV.items():
        if re.search(rf"(?<![a-z])\$?{abbrev}\b", text) and abbrev in re.findall(r"[a-z]+", text):
            return factor
    return 1.0


def matches(figure_value: float, unit: str | None, actual: float) -> bool:
    """Does `figure_value` as written (with its unit) equal `actual`, at the precision it was written?"""
    scale = _scale(unit)
    wanted = float(figure_value) * scale
    half_unit = 0.5 * 10 ** (-_decimals(figure_value)) * scale
    return abs(wanted - float(actual)) <= half_unit + 1e-9 * max(1.0, abs(wanted))


# ── What a tool result holds ───────────────────────────────────────────────────

_LABEL_KEYS = ("name", "concept", "tag", "series", "unit", "fp", "period_kind", "form")
# Numbers that describe a figure rather than being one: a fiscal year, a day count, how many bars an
# indicator read. Never offered as the number a figure could mean.
_METADATA_KEYS = {"fy", "duration_days", "bars_used", "seq", "call", "bars", "documents"}


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def numbers_in(result: Any) -> list[dict]:
    """
    Every number in a tool result, each with the words that say what it is:
    {"path", "value", "words"}. Indicators carry their name, series and parameters; filing facts
    their concept, tag and period; price rows their column and date.
    """
    items: list[dict] = []

    def walk(node: Any, path: str, words: set[str]) -> None:
        if isinstance(node, dict):
            if "columns" in node and isinstance(node.get("rows"), list):
                cols = node["columns"]
                for row in node["rows"]:
                    if isinstance(row, list) and len(row) == len(cols):
                        date = str(row[0])
                        for col, cell in zip(cols[1:], row[1:]):
                            if _is_number(cell):
                                items.append({"path": f"{path}[{date}].{col}", "value": float(cell),
                                              "words": words | _tokens(col) | _tokens(date)})
                return
            ctx = set(words)
            for key in _LABEL_KEYS:
                if isinstance(node.get(key), str):
                    ctx |= _tokens(node[key])
            params = node.get("params")
            if isinstance(params, dict):
                for k, v in params.items():
                    ctx |= _tokens(k) | (_tokens(v) if isinstance(v, (str, int)) and not isinstance(v, bool) else set())
            label = next((str(node[k]) for k in ("name", "concept") if isinstance(node.get(k), str)), None)
            here = f"{path}.{label}" if label else path
            for key, val in node.items():
                if key in _METADATA_KEYS:
                    continue
                if _is_number(val):
                    items.append({"path": f"{here}.{key}", "value": float(val), "words": ctx | _tokens(key)})
                elif isinstance(val, (dict, list)) and key != "params":
                    walk(val, f"{here}.{key}", ctx | _tokens(key))
        elif isinstance(node, list):
            for i, child in enumerate(node):
                walk(child, f"{path}[{i}]", words)

    walk(result, "result", set())
    return items


# ── The checks ─────────────────────────────────────────────────────────────────

def _call(record: dict, n: Any) -> dict | None:
    return next((c for c in record.get("tool_calls") or [] if c.get("seq") == n), None)


def check_figure(figure: dict, record: dict) -> dict:
    """One cited figure's check: {name, value, unit, ref, status, reason, ...}."""
    out = {"name": figure.get("name"), "value": figure.get("value"), "unit": figure.get("unit"),
           "ref": figure.get("ref")}
    ref = figure.get("ref") or {}
    if "judgment" in ref:
        return {**out, "status": JUDGMENT, "reason": "a judgment, with no figure behind it"}
    if "chunk" in ref:
        return {**out, "status": USER_ASSERTED,
                "reason": "taken from your document; shown, but never checked against the data"}
    n = ref.get("call")
    call = _call(record, n)
    if call is None:
        return {**out, "status": UNREFERENCED, "reason": f"there is no call {n}"}
    result = call.get("result")
    if isinstance(result, dict) and "error" in result:
        return {**out, "status": UNREFERENCED,
                "reason": f"call {n} ({call.get('tool')}) returned an error: {result['error']}"}

    items = numbers_in(result)
    unit = figure.get("unit")
    wanted = _tokens(figure.get("name") or "")
    # Words weigh ten times a number, so "sma_21" points at the moving averages, not at whatever else
    # in the call happens to have a 21 in it.
    scored = [(10 * len({w for w in wanted & it["words"] if not w.isdigit()})
               + len({w for w in wanted & it["words"] if w.isdigit()}), it) for it in items]
    best = max((s for s, _ in scored), default=0)
    named = [it for s, it in scored if s == best and best > 0]
    date = re.search(r"\d{4}-\d{2}-\d{2}", str(figure.get("name") or ""))
    if date and named and all("]." in it["path"] for it in named):
        # A price named for a date: only that day's bar will do.
        on_day = [it for it in named if f"[{date.group(0)}]" in it["path"]]
        if not on_day:
            return {**out, "status": MISMATCH, "reason": f"call {n} has no bar for {date.group(0)}",
                    "call_values": [{"path": it["path"], "value": it["value"]} for it in named[:3]]}
        named = on_day

    # A parameter in the name that the data doesn't have: "sma_21" when the call has the 20 and 50 day
    # means. Without this the 50-day mean's value would ground it. Years (four digits) are left out:
    # they are as likely to name a period as a parameter.
    params = {t for t in wanted if t.isdigit() and len(t) <= 3}
    if named and params and not any(params & it["words"] for it in named):
        have = sorted({w for it in named for w in it["words"] if w.isdigit() and len(w) <= 3})
        return {**out, "status": MISMATCH, "call_values": [{"path": it["path"], "value": it["value"]} for it in named[:3]],
                "reason": f"call {n} has no such figure with {', '.join(sorted(params))}"
                          + (f" (it has {', '.join(have)})" if have else "")}
    for it in named:
        if matches(figure["value"], unit, it["value"]):
            return {**out, "status": GROUNDED, "matched_by": "name", "matched": {"path": it["path"], "value": it["value"]},
                    "reason": f"matches {it['path']} in call {n}"}
    if named:
        shown = sorted({it["value"] for it in named})[:3]
        return {**out, "status": MISMATCH, "call_values": [{"path": it["path"], "value": it["value"]} for it in named[:3]],
                "reason": f"call {n} gives {', '.join(str(v) for v in shown)} for this, not {figure['value']}"
                          + (f" {unit}" if unit else "")}
    for it in items:
        if matches(figure["value"], unit, it["value"]):
            return {**out, "status": GROUNDED, "matched_by": "value", "matched": {"path": it["path"], "value": it["value"]},
                    "reason": f"the value matches {it['path']} in call {n}; its name matched nothing there"}
    return {**out, "status": UNREFERENCED, "reason": f"call {n} ({call.get('tool')}) holds nothing that matches this"}


def check_evidence(record: dict) -> list[dict]:
    """Key-point references that point at nothing: a call that doesn't exist or that returned an error."""
    problems = []
    for i, point in enumerate(((record.get("output") or {}).get("key_points")) or [], 1):
        for ref in point.get("evidence") or []:
            if "call" not in ref:
                continue
            call = _call(record, ref["call"])
            if call is None:
                problems.append({"key_point": i, "ref": ref, "reason": f"there is no call {ref['call']}"})
            elif isinstance(call.get("result"), dict) and "error" in call["result"]:
                problems.append({"key_point": i, "ref": ref,
                                 "reason": f"call {ref['call']} ({call.get('tool')}) returned an error"})
    return problems


def check_analysis(record: dict) -> dict | None:
    """
    The Tier 1 result for a finished analysis, or None for one with no signal (a halted run cites
    nothing). `grounded` over `total` counts the figures that can be checked: judgments and
    user-asserted figures are in neither.
    """
    output = record.get("output")
    if not output:
        return None
    checks = [check_figure(f, record) for f in output.get("cited_figures") or []]
    count = lambda s: sum(1 for c in checks if c["status"] == s)  # noqa: E731
    grounded, mismatched, unreferenced = count(GROUNDED), count(MISMATCH), count(UNREFERENCED)
    return {
        "version": CHECKS_VERSION,
        "grounded": grounded,
        "total": grounded + mismatched + unreferenced,
        "mismatched": mismatched,
        "unreferenced": unreferenced,
        "user_asserted": count(USER_ASSERTED),
        "judgment": count(JUDGMENT),
        "by_value_only": sum(1 for c in checks if c.get("matched_by") == "value"),
        "checks": checks,
        "evidence_problems": check_evidence(record),
    }
