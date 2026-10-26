"""
Planted-error benchmark: how often does the verification layer catch a figure we know is wrong?

    python scripts/planted_errors.py                 # writes logs/planted-errors-audit.md and .json
    python scripts/planted_errors.py --stdout        # print the Markdown report, write nothing

Agents are non-deterministic, so "assert output == expected" doesn't work on them. What can be
measured exactly is the *detector*: take answers real agents gave on real filings, plant one
error we know about in one figure, and rerun the same deterministic checks a compare run gets.

For each recorded compare run that kept its inputs (both conclusions, each agent's context, and
the filing facts it was given), every dollar or per-share figure an agent cited is altered, one
figure and one alteration at a time:

    plus_1pct, plus_10pct   the value off by 1% or 10%
    unit_slip               "billion" written as "million", or the reverse
    digit_swap              two adjacent digits transposed (a typing slip)

and, as controls that should NOT be caught:

    same_value              the same value written with an extra trailing zero
    rounded                 the value rounded to one fewer decimal place

Each altered answer goes through validation/facts.py (the figure-by-figure comparison against
the other agent), validation/constraints.py (the accounting checks, including each figure
against the filing value that agent was given) and validation/gate.py (what stops a run). The
outcome of one trial is:

    stopped   a new gate item about the altered figure: the run would wait for a human
    flagged   no gate item, but the figure's comparison row is newly flagged (shown, not stopped)
    missed    nothing about the altered figure changed

Nothing here calls a model or the network, and nothing is random: every run gives the same
counts. This measures the detector, not the agents, and only on the recorded runs listed in the
report. A figure the extractor doesn't recognise can't be altered here, so the benchmark also
can't see the extractor's own blind spots (ledger: checks-lexical-coverage).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation.constraints import run_checks  # noqa: E402
from validation.facts import compare_facts, context_facts, extract_facts  # noqa: E402
from validation.gate import gate_items  # noqa: E402

FIXTURES = ROOT / "web" / "frontend" / "tests" / "fixtures"
OUT_MD = ROOT / "logs" / "planted-errors-audit.md"
OUT_JSON = ROOT / "logs" / "planted-errors.json"
CHECKED_FAMILIES = ("currency", "per_share")  # the figures a filing lens gives an agent

# ─── Alterations ──────────────────────────────────────────────────────────────────

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _number(raw: str) -> tuple[re.Match, float, int] | None:
    m = _NUM.search(raw)
    if not m:
        return None
    text = m.group(0).replace(",", "")
    decimals = len(text.split(".")[1]) if "." in text else 0
    return m, float(text), decimals


def _with_value(raw: str, value: float, decimals: int) -> str | None:
    found = _number(raw)
    if not found:
        return None
    m = found[0]
    new = f"{value:,.{decimals}f}" if "," in m.group(0) else f"{value:.{decimals}f}"
    return raw[:m.start()] + new + raw[m.end():]


def _scaled(factor: float) -> Callable[[str], str | None]:
    def alter(raw: str) -> str | None:
        found = _number(raw)
        if not found:
            return None
        _, value, decimals = found
        # Keep the agent's precision unless that would hide the change entirely.
        for d in (decimals, decimals + 1, decimals + 2):
            out = _with_value(raw, value * factor, d)
            if out is not None and _number(out)[1] != value:
                return out
        return None
    return alter


def unit_slip(raw: str) -> str | None:
    for a, b in (("billion", "million"), ("million", "billion")):
        if re.search(rf"\b{a}\b", raw, re.I):
            return re.sub(rf"\b{a}\b", b, raw, count=1, flags=re.I)
    return None


def digit_swap(raw: str) -> str | None:
    found = _number(raw)
    if not found:
        return None
    m = found[0]
    digits = m.group(0)
    for i in range(len(digits) - 1):
        x, y = digits[i], digits[i + 1]
        if x.isdigit() and y.isdigit() and x != y:
            swapped = digits[:i] + y + x + digits[i + 2:]
            if swapped.lstrip("0") and not swapped.startswith("0"):
                return raw[:m.start()] + swapped + raw[m.end():]
    return None


def same_value(raw: str) -> str | None:
    found = _number(raw)
    if not found:
        return None
    m, _, decimals = found
    text = m.group(0)
    return raw[:m.end()] + ("0" if decimals else ".0") + raw[m.end():]


def rounded(raw: str) -> str | None:
    found = _number(raw)
    if not found or found[2] == 0:
        return None
    _, value, decimals = found
    out = _with_value(raw, round(value, decimals - 1), decimals - 1)
    return out if out != raw else None


ALTERATIONS: dict[str, tuple[Callable[[str], str | None], bool]] = {
    # name: (alteration, is_error). Controls (is_error False) should not be caught.
    "plus_1pct": (_scaled(1.01), True),
    "plus_10pct": (_scaled(1.10), True),
    "unit_slip": (unit_slip, True),
    "digit_swap": (digit_swap, True),
    "same_value": (same_value, False),
    "rounded": (rounded, False),
}

# ─── One trial ────────────────────────────────────────────────────────────────────


@dataclass
class Base:
    run_id: str
    ticker: str
    source: str
    conclusions: dict[str, str | None]
    contexts: dict[str, str]
    facts: dict[str, list[dict[str, Any]]]


@dataclass
class Trial:
    run_id: str
    slot: str
    metric: str
    family: str
    alteration: str
    is_error: bool
    original: str
    altered: str
    outcome: str = ""
    by: list[str] = field(default_factory=list)  # what stopped it: "comparison", "source check", other checks
    causes: list[str] = field(default_factory=list)  # for one that got through: facts about why
    sentence: str = ""


def evaluate(conclusions: dict[str, str | None], contexts: dict[str, str],
             facts: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """The same deterministic steps a compare run gets: figure rows, checks, gate items."""
    a, b = conclusions.get("a"), conclusions.get("b")
    rows: list[dict[str, Any]] = []
    status = "COMPARED" if a and b else "NOT_COMPARED"
    if a and b:
        rows = [r.to_dict() for r in compare_facts(
            extract_facts(a), extract_facts(b),
            context_facts(contexts.get("a")), context_facts(contexts.get("b")))]
    checks = run_checks(a, b, given_facts=facts)
    payload = {"cross_agent_comparison": {"status": status, "metric_comparisons": rows,
                                          "structural_flags": checks}}
    return {"rows": rows, "items": gate_items(payload), "checks": checks["checks"]}


def _item_key(item: dict[str, Any]) -> tuple:
    return item["kind"], item["metric"], item.get("status")


def _classify(trial: Trial, base_eval: dict[str, Any], new_eval: dict[str, Any], other_answered: bool) -> None:
    old = {_item_key(i) for i in base_eval["items"]}
    scope = f"agent_{trial.slot}"
    for item in new_eval["items"]:
        if _item_key(item) in old:
            continue
        if item["kind"] == "figure" and item["metric"] == trial.metric:
            trial.by.append("comparison")
        elif item["kind"] == "check" and item.get("scope") == scope and trial.metric in (item.get("metrics") or []):
            trial.by.append("source check" if ":matches_source" in item["metric"] or item["metric"].startswith("source:")
                            else "accounting check")
    if trial.by:
        trial.outcome = "stopped"
        return
    flagged_before = {r["metric"] for r in base_eval["rows"] if r.get("flags")}
    flagged_after = {r["metric"] for r in new_eval["rows"] if r.get("flags")}
    trial.outcome = "flagged" if trial.metric in flagged_after - flagged_before else "missed"
    trial.causes = _causes(trial, new_eval, other_answered)


def _causes(trial: Trial, new_eval: dict[str, Any], other_answered: bool) -> list[str]:
    """Checkable facts about why an altered figure got through (not a diagnosis)."""
    causes = []
    if "respectively" in trial.sentence.lower():
        causes.append("in a '…, respectively' list, where figures can be tagged to the wrong metric")
    if not other_answered:
        causes.append("only one agent answered, so there was nothing to compare against")
    scope = f"agent_{trial.slot}"
    if not any(c.get("rule") == "matches_source" and c.get("scope") == scope and trial.metric in (c.get("metrics") or [])
               for c in new_eval["checks"]):
        causes.append("no filing value for this metric was checked for this agent")
    if any(r["metric"] == trial.metric and r["status"] == "MATCH" for r in new_eval["rows"]):
        causes.append("another figure for the same metric still matched the other agent")
    if any(c.get("rule") == "matches_source" and c.get("scope") == scope and trial.metric in (c.get("metrics") or [])
           and c.get("outcome") == "pass" for c in new_eval["checks"]):
        causes.append("another figure for the same metric still matched the filing (one agreeing figure passes)")
    return causes


def run_base(base: Base) -> list[Trial]:
    base_eval = evaluate(base.conclusions, base.contexts, base.facts)
    trials: list[Trial] = []
    for slot in ("a", "b"):
        text = base.conclusions.get(slot)
        if not text:
            continue
        seen: set[tuple[int, int]] = set()
        for fact in extract_facts(text):
            if fact.family not in CHECKED_FAMILIES or fact.span in seen:
                continue
            seen.add(fact.span)
            start, end = fact.span
            for name, (alter, is_error) in ALTERATIONS.items():
                altered = alter(fact.raw)
                if not altered or altered == fact.raw:
                    continue
                changed = dict(base.conclusions)
                changed[slot] = text[:start] + altered + text[end:]
                trial = Trial(base.run_id, slot, fact.metric, fact.family, name, is_error,
                              fact.raw, altered, sentence=fact.sentence.strip())
                other = base.conclusions.get("b" if slot == "a" else "a")
                _classify(trial, base_eval, evaluate(changed, base.contexts, base.facts), bool(other))
                trials.append(trial)
    return trials


# ─── The recorded runs ────────────────────────────────────────────────────────────


def load_bases(folder: Path = FIXTURES) -> list[Base]:
    """Recorded ticker compares that kept their inputs, at auditor scope, one per run id."""
    bases: dict[str, Base] = {}
    for path in sorted(folder.glob("run_compare_*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        cmp = run.get("cross_agent_comparison") or {}
        conclusions = {"a": cmp.get("agent_a_conclusion"), "b": cmp.get("agent_b_conclusion")}
        facts = run.get("facts") or {}
        if not run.get("ticker") or not any(conclusions.values()) or not any((facts or {}).values()):
            continue
        if run["run_id"] in bases:
            continue  # the same run captured at another scope
        bases[run["run_id"]] = Base(run["run_id"], run["ticker"], path.name, conclusions,
                                    run.get("contexts") or {}, facts)
    return list(bases.values())


# ─── Report ───────────────────────────────────────────────────────────────────────


def summarize(trials: list[Trial]) -> dict[str, Any]:
    by_alteration: dict[str, dict[str, int]] = {}
    for t in trials:
        row = by_alteration.setdefault(t.alteration, {"planted": 0, "stopped": 0, "flagged": 0, "missed": 0,
                                                      "comparison": 0, "source check": 0, "accounting check": 0})
        row["planted"] += 1
        row[t.outcome] += 1
        for d in set(t.by):
            row[d] += 1
    errors = [t for t in trials if t.is_error]
    controls = [t for t in trials if not t.is_error]
    return {
        "by_alteration": by_alteration,
        "errors": {"planted": len(errors), "stopped": sum(t.outcome == "stopped" for t in errors),
                   "flagged": sum(t.outcome == "flagged" for t in errors),
                   "missed": sum(t.outcome == "missed" for t in errors)},
        "controls": {"planted": len(controls), "stopped": sum(t.outcome == "stopped" for t in controls),
                     "flagged": sum(t.outcome == "flagged" for t in controls)},
    }


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def to_markdown(bases: list[Base], trials: list[Trial], summary: dict[str, Any]) -> str:
    e, c = summary["errors"], summary["controls"]
    out = [
        "# Planted-error audit",
        "",
        "Generated by `scripts/planted_errors.py`. It is deterministic, calls no model and no network,",
        "and rerunning it gives the same counts. It measures the detector (comparison, accounting checks,",
        "gate), not the agents. This is an audit for human judgment, not a verdict.",
        "",
        "## Result",
        "",
        f"- **Planted errors:** {e['planted']}. **Stopped the run:** {e['stopped']} ({_pct(e['stopped'], e['planted'])}).",
        f"  **Flagged only:** {e['flagged']}. **Missed:** {e['missed']} ({_pct(e['missed'], e['planted'])}).",
        f"- **Controls** (edits that should not be caught): {c['planted']}. **Stopped anyway:** {c['stopped']}"
        f" ({_pct(c['stopped'], c['planted'])}). **Flagged:** {c['flagged']}.",
        "",
        "## By alteration",
        "",
        "| Alteration | Kind | Planted | Stopped | Flagged only | Missed | Stopped by comparison | by source check | by other checks |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, (_, is_error) in ALTERATIONS.items():
        r = summary["by_alteration"].get(name)
        if not r:
            continue
        out.append(f"| `{name}` | {'error' if is_error else 'control'} | {r['planted']} | {r['stopped']} | "
                   f"{r['flagged']} | {r['missed']} | {r['comparison']} | {r['source check']} | {r['accounting check']} |")
    out += ["", "A trial can be stopped by more than one detector, so the last three columns can add up to more",
            "than Stopped.", "", "## Errors that got through", ""]
    through = [t for t in trials if t.is_error and t.outcome != "stopped"]
    if not through:
        out.append("None.")
    else:
        out += ["| Run | Agent | Metric | Alteration | Original → altered | Outcome | Why it got through (checked facts) |",
                "|---|---|---|---|---|---|---|"]
        out += [f"| `{t.run_id[:8]}` | {t.slot.upper()} | `{t.metric}` | `{t.alteration}` | `{t.original}` → "
                f"`{t.altered}` | {t.outcome} | {'; '.join(t.causes) or '-'} |" for t in through]
        tally: dict[str, int] = {}
        for t in through:
            for cause in t.causes:
                tally[cause] = tally.get(cause, 0) + 1
        out += ["", "How often each fact applies to the errors that got through (one error can have several):", ""]
        out += [f"- {n} of {len(through)}: {cause}" for cause, n in sorted(tally.items(), key=lambda kv: -kv[1])]
    false_stops = [t for t in trials if not t.is_error and t.outcome == "stopped"]
    out += ["", "## Controls that were stopped", ""]
    if not false_stops:
        out.append("None.")
    else:
        out += ["| Run | Agent | Metric | Alteration | Original → altered | Stopped by |", "|---|---|---|---|---|---|"]
        out += [f"| `{t.run_id[:8]}` | {t.slot.upper()} | `{t.metric}` | `{t.alteration}` | `{t.original}` → "
                f"`{t.altered}` | {', '.join(sorted(set(t.by)))} |" for t in false_stops]
    out += ["", "## The recorded runs used", "",
            "| Run | Ticker | Fixture | Agents with an answer |", "|---|---|---|---|"]
    out += [f"| `{b.run_id[:8]}` | {b.ticker} | `{b.source}` | "
            f"{', '.join(s.upper() for s, v in b.conclusions.items() if v)} |" for b in bases]
    out += ["", "## What this doesn't show", "",
            "- **The agents' accuracy.** Every error here was planted; the agents' own mistakes are counted",
            "  by live runs, not by this audit.",
            "- **The extractor's blind spots.** Only figures the extractor already recognises can be",
            "  altered, so a figure written in a form it doesn't parse is never tested here",
            "  (ledger `checks-lexical-coverage`).",
            f"- **A large sample.** {len(bases)} recorded runs; the counts are trials over their figures,",
            "  not independent runs.", ""]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stdout", action="store_true", help="print the Markdown report; write nothing")
    args = parser.parse_args()
    bases = load_bases()
    trials = [t for b in bases for t in run_base(b)]
    summary = summarize(trials)
    md = to_markdown(bases, trials, summary)
    if args.stdout:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")  # the report uses arrows; Windows consoles default to cp1252
        print(md)
        return 0
    OUT_MD.write_text(md, encoding="utf-8", newline="\n")
    OUT_JSON.write_text(json.dumps({"summary": summary, "trials": [t.__dict__ for t in trials],
                                    "runs": [b.run_id for b in bases]}, indent=1) + "\n",
                        encoding="utf-8", newline="\n")
    e = summary["errors"]
    print(f"{e['planted']} planted errors: {e['stopped']} stopped, {e['flagged']} flagged only, "
          f"{e['missed']} missed; controls stopped: {summary['controls']['stopped']}/{summary['controls']['planted']}")
    print(f"wrote {OUT_MD.relative_to(ROOT)} and {OUT_JSON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
