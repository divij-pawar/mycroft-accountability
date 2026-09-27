"""
B5 — why two agents' views differ, what each has behind it, and whether there is a
grade to propose.

Inputs are things this subsystem already produced and recorded: the figure-by-figure
rows (B1), the accounting checks (B3) and each agent's assessment (B4, a model
judgment). Nothing here calls a model, and nothing here decides: it classifies,
counts, and proposes. A grade reaches an investor only when a human records one
(validation/gate.py's set_grade), per P1.

Divergence classes (deterministic, in this order):
  data        the agents' figures conflict — a MISMATCH row, or the same metric
              stated for different periods
  assumption  same figures, different stated assumptions (revenue growth more than
              a point apart, a different margin trend or horizon)
  weighting   the grades or directions differ and neither of the above explains it:
              the same evidence, weighed differently. Named as a residual, never as
              a finding about which agent is right.
  none        no difference in grade or direction, and no conflicting figure

Provenance strength is counted, never scored: figures that match the filing the
agent was given, figures that contradict it, figures cited with nothing to check
them against, and failed hard checks. No weights, no index (P3: a number no record
produced is not evidence).

consensus_grade is set only when both agents gave the same grade AND direction and
no hard check failed on either; otherwise it is None with the reason. When both
gave grades and they differ, `needs_decision` is set, which the gate turns into a
"set the grade" item (gate policy v3).
"""

from __future__ import annotations

from typing import Any

SYNTHESIS_POLICY = "b5-v1"

_DATA_STATUSES = {"MISMATCH", "DIFFERENT_PERIODS"}
_GROWTH_TOLERANCE_PTS = 1.0


def _counts(slot: str, rows: list[dict], checks: list[dict]) -> dict[str, int]:
    scope = f"agent_{slot}"
    source = [c for c in checks if c.get("scope") == scope and c.get("rule") == "matches_source"]
    checked = {m for c in source for m in c.get("metrics") or []}
    cited = [r for r in rows if r.get(f"raw_{slot}") is not None]
    return {
        "match_filing": sum(c.get("outcome") == "pass" for c in source),
        "contradict_filing": sum(c.get("outcome") == "fail" for c in source),
        "unchecked": sum(1 for r in cited if r.get("metric") not in checked and r.get("status") != "UNCORROBORATED"),
        "unbacked": sum(1 for r in cited if r.get("status") in ("UNCORROBORATED", "DERIVED_WRONG")),
        "hard_check_failures": sum(1 for c in checks if c.get("scope") == scope and c.get("kind") == "hard"
                                   and c.get("outcome") == "fail"),
    }


def _assumption_differences(a: dict, b: dict) -> list[dict[str, Any]]:
    aa, bb = a.get("assumptions") or {}, b.get("assumptions") or {}
    out = []
    ga, gb = aa.get("revenue_growth_pct"), bb.get("revenue_growth_pct")
    if ga is not None and gb is not None and abs(ga - gb) > _GROWTH_TOLERANCE_PTS:
        out.append({"key": "revenue_growth_pct", "a": ga, "b": gb})
    for key in ("margin_trend", "horizon_months"):
        if aa.get(key) is not None and bb.get(key) is not None and aa[key] != bb[key]:
            out.append({"key": key, "a": aa[key], "b": bb[key]})
    return out


def _say(value: Any, key: str) -> str:
    return f"{value}% revenue growth" if key == "revenue_growth_pct" else \
        f"a {value}-month horizon" if key == "horizon_months" else f"{value} margins"


def synthesize(
    rows: list[dict] | None,
    structural_flags: dict | None,
    assessments: dict[str, dict | None],
) -> dict[str, Any]:
    """
    The synthesis for one compare run. `assessments` maps "a"/"b" to that agent's
    validated assessment dict (or None when it gave none).
    """
    rows = rows or []
    checks = (structural_flags or {}).get("checks") or []
    a, b = assessments.get("a") or {}, assessments.get("b") or {}

    candidates = [
        {"slot": slot, "grade": x.get("grade"), "direction": x.get("direction"),
         "assumptions": x.get("assumptions") or {}, "evidence": _counts(slot, rows, checks)}
        for slot, x in (("a", a), ("b", b))
    ]
    data_rows = [r for r in rows if r.get("status") in _DATA_STATUSES]
    assumptions = _assumption_differences(a, b) if a and b else []
    graded = bool(a.get("grade")) and bool(b.get("grade"))
    views_differ = graded and (a.get("grade") != b.get("grade") or a.get("direction") != b.get("direction"))
    hard = any(c["evidence"]["hard_check_failures"] for c in candidates)

    if data_rows:
        driver = "data"
        labels = ", ".join(r.get("label", r.get("metric")) for r in data_rows[:3])
        recommendation = (f"The agents' figures conflict ({labels}). Settle which figure the filing supports "
                          "before weighing either agent's grade.")
    elif assumptions:
        driver = "assumption"
        first = assumptions[0]
        recommendation = (f"Same figures, different assumptions: agent A assumes {_say(first['a'], first['key'])}, "
                          f"agent B assumes {_say(first['b'], first['key'])}. Decide which assumption the evidence supports.")
    elif views_differ:
        driver = "weighting"
        recommendation = ("The grades differ, but not because of a conflicting figure or a stated assumption: the "
                          "agents weighed the same evidence differently. Read both answers and set the grade.")
    elif not graded:
        driver = "insufficient"
        recommendation = ("Only one agent gave a grade, so there is nothing to reconcile."
                          if a.get("grade") or b.get("grade") else "Neither agent gave a grade.")
    else:
        driver = "none"
        recommendation = ("Both agents give the same grade and direction."
                          + (" A hard accounting check failed, so it is not a consensus yet." if hard
                             else " A reviewer can confirm it."))

    if graded and not views_differ and not hard:
        consensus, reason = {"grade": a["grade"], "direction": a["direction"]}, None
    elif not graded:
        consensus, reason = None, "Both agents must give a grade for there to be a consensus."
    elif views_differ:
        consensus, reason = None, "The agents' grades or directions differ."
    else:
        consensus, reason = None, "A hard accounting check failed on one of the agents' figures."

    return {
        "policy": SYNTHESIS_POLICY,
        "primary_conflict_driver": driver,
        "data_conflicts": [r.get("metric") for r in data_rows],
        "assumption_differences": assumptions,
        "grade_candidates": candidates,
        "consensus_grade": consensus,
        "consensus_reason": reason,
        "audit_recommendation": recommendation,
        # A human must set the grade when both agents graded and they disagree.
        "needs_decision": views_differ,
    }
