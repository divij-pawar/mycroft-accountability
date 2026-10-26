"""
Planted-error benchmark for analyses: how often does Tier 1 catch a figure we know is wrong?

    python scripts/planted_analysis_errors.py            # writes logs/planted-analysis-errors-audit.md and .json
    python scripts/planted_analysis_errors.py --stdout   # print the Markdown report, write nothing

The compare benchmark (scripts/planted_errors.py) alters figures in answers real agents gave. No
real analysis has been recorded yet, so this one starts from the other end: it takes the results of
the real data tools over the recorded AAPL bundle, writes down every figure an agent could
correctly cite from them (exactly as the tool returned it, with the unit it would give), checks
that Tier 1 passes every one, then plants one error at a time and checks again.

    plus_1pct, plus_10pct, minus_10pct   the value off by 1% or 10%
    digit_swap                           two adjacent digits transposed (a typing slip)
    unit_slip                            "billion" written as "million", or the reverse (filing figures)
    sign_flip                            a negative figure written as positive
    wrong_call                           the right value, but citing a call that holds no such number
    missing_call                         the right value, citing a call that doesn't exist
    neighbour_value                      the value of a different figure with the same name words
                                         (the weekly RSI quoted as the daily one)

and, as controls that should NOT be caught:

    same_value                           the figure exactly as given
    rounded                              the value rounded to one fewer decimal place

An outcome is `caught` when Tier 1 calls the altered figure MISMATCH or UNREFERENCED, `missed`
when it still calls it GROUNDED. Controls are `kept` when still GROUNDED, `wrongly_caught` otherwise.

Nothing here calls a model or the network and nothing is random, so every run gives the same counts.
This measures the detector on figures whose truth we know. It says nothing about how often a real
agent makes these errors, and a figure the checker can't read isn't one it can be tested on.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.analysis import RECORD_SCHEMA  # noqa: E402
from financial_agent.tools import ToolRecorder  # noqa: E402
from validation.analysis_checks import GROUNDED, MISMATCH, UNREFERENCED, check_figure  # noqa: E402

LOGS = ROOT / "logs"
OUT_MD = LOGS / "planted-analysis-errors-audit.md"
OUT_JSON = LOGS / "planted-analysis-errors.json"

ERROR_KINDS = ("plus_1pct", "plus_10pct", "minus_10pct", "digit_swap", "unit_slip", "sign_flip",
               "wrong_call", "missing_call", "neighbour_value")
CONTROL_KINDS = ("same_value", "rounded")


# ── The correct figures ────────────────────────────────────────────────────────

def _decimals(v: float) -> int:
    from decimal import Decimal
    exp = Decimal(repr(float(v))).as_tuple().exponent
    return max(0, -exp) if isinstance(exp, int) else 0


def _indicator_name(r: dict, key: str) -> str:
    parts = [r["name"]]
    for p in r["params"].values():
        if isinstance(p, (int, float)) and not isinstance(p, bool):
            parts.append(str(p))
    if key != "value":
        parts.append(key)
    if r["params"].get("series") == "weekly":
        parts.append("weekly")
    return "_".join(parts)


def correct_figures(recorder: ToolRecorder) -> list[dict]:
    """
    Every figure an agent could correctly cite from calls 2-4, as it would write it:
    {"name", "value", "unit", "call", "group", "scale"}. Call 1 (describe_data) is the call a figure is
    mis-cited to, because it holds no price, indicator or filing figure.
    """
    out: list[dict] = []
    ind = next(c for c in recorder.calls if c["tool"] == "get_indicators")
    for r in ind["result"]["results"]:
        if r.get("status") != "ok":
            continue
        for key, v in (r.get("values") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append({"name": _indicator_name(r, key), "value": v, "unit": r.get("unit"), "call": ind["seq"],
                            "group": "indicator", "scale": 1.0, "indicator": r["name"], "series": r["params"].get("series")})
    facts = next(c for c in recorder.calls if c["tool"] == "get_filing_facts")
    for f in facts["result"]["facts"]:
        if f.get("unit") == "USD":
            out.append({"name": f["concept"], "value": round(f["value"] / 1e9, 3), "unit": "USD billion",
                        "call": facts["seq"], "group": "filing", "scale": 1e9})
        else:
            out.append({"name": f["concept"], "value": f["value"], "unit": f["unit"], "call": facts["seq"],
                        "group": "filing", "scale": 1.0})
    prices = next(c for c in recorder.calls if c["tool" ] == "get_prices")
    for row in prices["result"]["rows"]:
        out.append({"name": f"close_{row[0]}", "value": row[4], "unit": "USD", "call": prices["seq"], "group": "price", "scale": 1.0})
        out.append({"name": f"volume_{row[0]}", "value": row[5], "unit": None, "call": prices["seq"], "group": "price", "scale": 1.0})
    return out


# ── The alterations ────────────────────────────────────────────────────────────

def _same_precision(v: float, decimals: int) -> float:
    return round(v, decimals) if decimals else float(round(v))


def digit_swap(v: float) -> float | None:
    """Two adjacent different digits swapped, keeping the digits' places; None if no swap changes the number."""
    text = repr(float(v)) if _decimals(v) else str(int(v))
    digits = [i for i, ch in enumerate(text) if ch.isdigit()]
    for a, b in zip(digits, digits[1:]):
        if b == a + 1 and text[a] != text[b] :
            swapped = text[:a] + text[b] + text[a] + text[b + 1:]
            try:
                out = float(swapped) if "." in swapped else int(swapped)
            except ValueError:
                continue
            if out != v:
                return out
    return None


def alterations(fig: dict, others: list[dict]) -> dict[str, Callable[[], dict | None]]:
    """The planted errors for one correct figure, each a function returning the altered figure (or None if it doesn't apply)."""
    v, d = fig["value"], _decimals(fig["value"])
    base = {k: fig[k] for k in ("name", "value", "unit", "call")}

    def scaled(factor):
        return lambda: {**base, "value": _same_precision(v * factor, d)} if _same_precision(v * factor, d) != v else None

    def unit_slip():
        if fig["unit"] != "USD billion":
            return None
        return {**base, "unit": "USD million"}

    def sign_flip():
        return {**base, "value": -v} if v < 0 else None

    def swap():
        out = digit_swap(v)
        return {**base, "value": out} if out is not None else None

    def wrong_call():
        return {**base, "call": 1}

    def missing_call():
        return {**base, "call": 99}

    def neighbour():
        same = [o for o in others if o is not fig and o["group"] == fig["group"] == "indicator"
                and o["indicator"] == fig["indicator"] and abs(o["value"] - v) > 0.5 * 10 ** (-d) + 1e-9]
        return {**base, "value": same[0]["value"]} if same else None

    return {"plus_1pct": scaled(1.01), "plus_10pct": scaled(1.10), "minus_10pct": scaled(0.90), "digit_swap": swap,
            "unit_slip": unit_slip, "sign_flip": sign_flip, "wrong_call": wrong_call, "missing_call": missing_call,
            "neighbour_value": neighbour}


def controls(fig: dict) -> dict[str, Callable[[], dict | None]]:
    base = {k: fig[k] for k in ("name", "value", "unit", "call")}
    d = _decimals(fig["value"])
    return {"same_value": lambda: dict(base),
            "rounded": lambda: {**base, "value": _same_precision(fig["value"], d - 1)} if d >= 1 else None}


# ── Running it ─────────────────────────────────────────────────────────────────

def recorded_calls() -> tuple[ToolRecorder, dict]:
    """The recorded AAPL bundle and the four calls every figure is cited to or from."""
    from tests.test_fa_agent import _bundle
    bundle = _bundle()
    rec = ToolRecorder(bundle)
    rec.call("a", "describe_data")
    rec.call("a", "get_indicators")
    rec.call("a", "get_filing_facts")
    rec.call("a", "get_prices", {"last": 3})
    return rec, bundle


def run() -> dict[str, Any]:
    rec, bundle = recorded_calls()
    record = {"schema": RECORD_SCHEMA, "tool_calls": rec.calls, "output": {"key_points": []}}
    figures = correct_figures(rec)

    def status(figure: dict) -> dict:
        return check_figure({"name": figure["name"], "value": figure["value"], "unit": figure.get("unit"),
                             "ref": {"call": figure["call"]}}, record)

    baseline = [(f, status(f)) for f in figures]
    not_readable = [f"{f['name']} ({r['status']}: {r['reason']})" for f, r in baseline if r["status"] != GROUNDED]
    usable = [f for f, r in baseline if r["status"] == GROUNDED]

    trials: list[dict] = []
    for fig in usable:
        for kind, make in alterations(fig, usable).items():
            altered = make()
            if altered is None:
                continue
            r = status(altered)
            trials.append({"kind": kind, "group": fig["group"], "name": fig["name"], "true_value": fig["value"],
                           "altered": {k: altered[k] for k in ("value", "unit", "call")}, "status": r["status"],
                           "outcome": "caught" if r["status"] in (MISMATCH, UNREFERENCED) else "missed", "reason": r["reason"]})
        for kind, make in controls(fig).items():
            altered = make()
            if altered is None:
                continue
            r = status(altered)
            trials.append({"kind": kind, "group": fig["group"], "name": fig["name"], "true_value": fig["value"],
                           "altered": {k: altered[k] for k in ("value", "unit", "call")}, "status": r["status"],
                           "outcome": "kept" if r["status"] == GROUNDED else "wrongly_caught", "reason": r["reason"]})
    return {"bundle_hash": bundle["bundle_hash"], "as_of": bundle["data"]["as_of"], "figures": len(figures),
            "figures_usable": len(usable), "figures_not_readable": not_readable, "trials": trials,
            "summary": summarize(trials)}


def summarize(trials: list[dict]) -> dict[str, Any]:
    errors = [t for t in trials if t["kind"] in ERROR_KINDS]
    ctrl = [t for t in trials if t["kind"] in CONTROL_KINDS]
    by_kind: dict[str, dict] = {}
    for kind in ERROR_KINDS + CONTROL_KINDS:
        ts = [t for t in trials if t["kind"] == kind]
        if ts:
            by_kind[kind] = dict(Counter(t["outcome"] for t in ts), total=len(ts))
    by_group: dict[str, dict] = defaultdict(lambda: {"caught": 0, "missed": 0})
    for t in errors:
        by_group[t["group"]][t["outcome"]] += 1
    return {"errors": len(errors), "caught": sum(1 for t in errors if t["outcome"] == "caught"),
            "missed": sum(1 for t in errors if t["outcome"] == "missed"),
            "controls": len(ctrl), "controls_wrongly_caught": sum(1 for t in ctrl if t["outcome"] == "wrongly_caught"),
            "by_kind": by_kind, "by_group": dict(by_group)}


def to_markdown(result: dict) -> str:
    s = result["summary"]
    pct = lambda n, d: f"{n / d:.0%}" if d else "n/a"  # noqa: E731
    lines = [
        "# Planted-error audit: analyses",
        "",
        f"Bundle `{result['bundle_hash'][:19]}` (AAPL, data as of {result['as_of']}). {result['figures']} figures an agent could "
        f"correctly cite from the four recorded tool calls, {result['figures_usable']} of which Tier 1 passes as written; "
        "each had one error planted at a time, and the same checks were rerun.",
        "",
        "This measures the detector on figures whose truth is known. It says nothing about how often a real agent makes these "
        "errors, and a figure the checker can't read can't be tested on (listed below).",
        "",
        f"**Errors:** {s['caught']} of {s['errors']} caught ({pct(s['caught'], s['errors'])}), {s['missed']} missed.  ",
        f"**Controls** (should not be caught): {s['controls_wrongly_caught']} of {s['controls']} wrongly caught.",
        "",
        "| Planted error | Trials | Caught | Missed |", "|---|---|---|---|",
    ]
    for kind in ERROR_KINDS:
        k = s["by_kind"].get(kind)
        if k:
            lines.append(f"| `{kind}` | {k['total']} | {k.get('caught', 0)} | {k.get('missed', 0)} |")
    lines += ["", "| Control | Trials | Kept | Wrongly caught |", "|---|---|---|---|"]
    for kind in CONTROL_KINDS:
        k = s["by_kind"].get(kind)
        if k:
            lines.append(f"| `{kind}` | {k['total']} | {k.get('kept', 0)} | {k.get('wrongly_caught', 0)} |")
    lines += ["", "| Figure group | Caught | Missed |", "|---|---|---|"]
    for g, c in sorted(s["by_group"].items()):
        lines.append(f"| {g} | {c['caught']} | {c['missed']} |")
    missed = [t for t in result["trials"] if t["outcome"] == "missed"]
    lines += ["", f"## Missed ({len(missed)})", ""]
    if not missed:
        lines.append("None.")
    else:
        causes = Counter(t["kind"] for t in missed)
        lines.append("By planted error: " + ", ".join(f"`{k}` x{v}" for k, v in causes.most_common()) + ".")
        lines += ["", "| Planted error | Figure | True value | Written as | Why Tier 1 let it through |", "|---|---|---|---|---|"]
        for t in missed[:40]:
            a = t["altered"]
            lines.append(f"| `{t['kind']}` | `{t['name']}` | {t['true_value']} | {a['value']}{' ' + a['unit'] if a['unit'] else ''}, call {a['call']} | {t['reason']} |")
        if len(missed) > 40:
            lines.append(f"| ... | {len(missed) - 40} more in the JSON | | | |")
    wrongly = [t for t in result["trials"] if t["outcome"] == "wrongly_caught"]
    if wrongly:
        lines += ["", f"## Controls wrongly caught ({len(wrongly)})", ""]
        for t in wrongly[:20]:
            lines.append(f"- `{t['name']}` written as {t['altered']['value']}: {t['reason']}")
    if result["figures_not_readable"]:
        lines += ["", "## Figures Tier 1 does not pass as written (not tested)", ""]
        lines += [f"- {x}" for x in result["figures_not_readable"]]
    return "\n".join(lines) + "\n"


def _shown(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stdout", action="store_true", help="print the report and write nothing")
    args = ap.parse_args()
    result = run()
    report = to_markdown(result)
    if args.stdout:
        print(report)
        return 0
    LOGS.mkdir(exist_ok=True)
    OUT_MD.write_text(report, encoding="utf-8")
    OUT_JSON.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    s = result["summary"]
    print(f"{s['caught']} of {s['errors']} planted errors caught, {s['missed']} missed; "
          f"{s['controls_wrongly_caught']} of {s['controls']} controls wrongly caught")
    print(f"wrote {_shown(OUT_MD)}, {_shown(OUT_JSON)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
