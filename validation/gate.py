"""
BG — the human decision gate for cross-agent runs (SNICKERDOODLE P1, P4, P7).

A compare run whose figure-by-figure check (validation/facts.py) finds a MISMATCH
does not resolve itself. It enters AWAITING_DECISION and stays there until a named
human has recorded a decision for every mismatched figure. Until then, investor-
scope reads get "pending human review" in place of the contested figures and the
conclusions that state them (redact_for_scope below).

What the gate is, precisely (P4's "specific, testable handoff condition"):

  gated items  = the run's metric_comparisons rows with status MISMATCH, plus
                 (policy v2, B3) every failed HARD accounting check about an
                 agent's own figures (validation/constraints.py: an agent
                 misquoting a filing value it was given, or figures that break a
                 definitional identity)
  cleared when = every gated item is cited by at least one recorded decision

Nothing else opens it. UNCORROBORATED rows raise contradiction_flag but are not a
conflict between the agents, so they are shown for review and never gate; nor do
heuristic check warnings or checks on the filing itself. B5's "no consensus" is the
later trigger the plan names; it is not implemented here.

Decisions live in web/db.py's gate_decisions table, which is append-only. A later
decision that cites the same figure supersedes the earlier one; both stay in the
history. This module holds no database code — the caller passes the decisions in —
so it is testable without SQLite.

Runs stored before this gate existed carry no "gate_policy" key and are NOT_GATED:
they are never gated retroactively (the approved plan's rule), even if their rows
contain a MISMATCH.

Identity: the JWT carries a scope, not a person, so decided_by is a self-declared
name. Every gate payload says so (IDENTITY_NOTE); real identity is a separate auth
project.
"""

from __future__ import annotations

import copy
from typing import Any, Iterable

# v1 (BG): mismatched figures gate. v2 (B3, 2026-09-25): failed hard checks too.
# v3 (B5, 2026-09-26): both agents graded and disagree — a human sets the grade.
# A run is gated under the policy it was stored with; nothing is re-gated later.
GATE_POLICY = "v3"
GRADES = ("AAA", "AA", "A", "BBB", "BB", "B", "CCC")  # core/assessment.py's closed vocabulary

# Row statuses that open the gate. Deliberately only a real two-sided conflict.
GATING_STATUSES = frozenset({"MISMATCH"})

DECISIONS: dict[str, str] = {
    "accept_a":        "Agent A's figure is right",
    "accept_b":        "Agent B's figure is right",
    "both_wrong":      "Both figures are wrong",
    "not_a_conflict":  "Not a real conflict",
    "override_value":  "Set the correct value",
    "confirmed_error": "The check is right: the agent's figure is wrong",
    "set_grade":       "Set the grade",
}

# Which decisions make sense for which kind of item. A decision citing items of
# both kinds may only use a decision valid for all of them.
DECISIONS_FOR: dict[str, frozenset[str]] = {
    "figure": frozenset({"accept_a", "accept_b", "both_wrong", "not_a_conflict", "override_value"}),
    "check":  frozenset({"confirmed_error", "not_a_conflict", "override_value"}),
    # accept_a / accept_b adopt that agent's grade; set_grade records a different one.
    "grade":  frozenset({"accept_a", "accept_b", "set_grade"}),
}

MIN_RATIONALE = 20
MIN_NAME = 2

IDENTITY_NOTE = "decided_by is the name the reviewer typed; it is recorded as entered and not authenticated."

NOT_GATED = "NOT_GATED"                     # stored before the gate existed
NO_DECISION_NEEDED = "NO_DECISION_NEEDED"   # compared, nothing mismatched (or not compared at all)
AWAITING_DECISION = "AWAITING_DECISION"
DECIDED = "DECIDED"


class DecisionError(ValueError):
    """A decision that must not be recorded, with the reason in words."""


def gated_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cmp = payload.get("cross_agent_comparison") or {}
    if cmp.get("status") != "COMPARED":
        return []
    return [r for r in (cmp.get("metric_comparisons") or []) if r.get("status") in GATING_STATUSES]


def gated_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cmp = payload.get("cross_agent_comparison") or {}
    flags = cmp.get("structural_flags") or {}
    return [c for c in flags.get("checks") or [] if c.get("gates")]


def gate_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Everything that needs a decision, keyed the way decisions cite it."""
    items = [
        {"metric": r["metric"], "kind": "figure", "label": r.get("label", r["metric"]),
         "status": r["status"], "metrics": [r["metric"]]}
        for r in gated_rows(payload)
    ]
    items += [
        {"metric": c["key"], "kind": "check", "label": f"{c.get('who', c['scope'])}: {c['plain']}",
         "status": "CHECK_FAILED", "metrics": c.get("metrics") or [], "scope": c["scope"],
         "math": c.get("math")}
        for c in gated_checks(payload)
    ]
    synthesis = (payload.get("cross_agent_comparison") or {}).get("synthesis") or {}
    if synthesis.get("needs_decision"):
        cands = {c["slot"]: c for c in synthesis.get("grade_candidates") or []}
        items.append({
            "metric": "grade", "kind": "grade", "status": "NO_CONSENSUS", "metrics": [],
            "label": "The agents' grades differ: set the grade",
            "candidates": {s: {"grade": c.get("grade"), "direction": c.get("direction"),
                               "assumptions": c.get("assumptions") or {}} for s, c in cands.items()},
            "driver": synthesis.get("primary_conflict_driver"),
        })
    return items


def validate_decision(payload: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """
    Check a proposed decision against the run it is about. Returns the cleaned
    fields to store; raises DecisionError otherwise. Never coerces a bad value
    into a good one (P3): a wrong field is refused, not repaired.
    """
    if payload.get("gate_policy") is None:
        raise DecisionError("This run was stored before the decision gate existed, so it has nothing to decide.")
    rows = {i["metric"]: i for i in gate_items(payload)}
    if not rows:
        raise DecisionError("This run has no mismatched figures or failed checks, so there is nothing to decide.")

    decision = body.get("decision")
    if decision not in DECISIONS:
        raise DecisionError(f"Unknown decision {decision!r}. Use one of: {', '.join(DECISIONS)}.")

    name = (body.get("decided_by") or "").strip()
    if len(name) < MIN_NAME:
        raise DecisionError("Say who is deciding: a name of at least 2 characters.")

    rationale = (body.get("rationale") or "").strip()
    if len(rationale) < MIN_RATIONALE:
        raise DecisionError(f"Give your reasoning in at least {MIN_RATIONALE} characters.")

    cited = body.get("cited_items") or []
    if not isinstance(cited, list) or not cited:
        raise DecisionError("Pick at least one mismatched figure this decision is about.")
    unknown = [c for c in cited if c not in rows]
    if unknown:
        raise DecisionError(f"Not a mismatched figure or failed check in this run: {', '.join(map(str, unknown))}.")
    cited = list(dict.fromkeys(cited))  # keep order, drop repeats
    kinds = {rows[c]["kind"] for c in cited}
    if not all(decision in DECISIONS_FOR[k] for k in kinds):
        allowed = frozenset.intersection(*(DECISIONS_FOR[k] for k in kinds))
        what = ("a failed check" if kinds == {"check"} else
                "a disputed figure" if kinds == {"figure"} else "this mix of items")
        raise DecisionError(
            f'"{DECISIONS[decision]}" doesn\'t apply to {what}. '
            f"Use one of: {', '.join(DECISIONS[d] for d in sorted(allowed))}.")

    final_grade = body.get("final_grade")
    if decision == "set_grade":
        if final_grade not in GRADES:
            raise DecisionError(f"Set the grade to one of {', '.join(GRADES)}.")
    elif final_grade is not None:
        raise DecisionError("Only \"Set the grade\" takes a grade.")
    if decision in ("accept_a", "accept_b") and kinds == {"grade"}:
        slot = decision[-1]
        final_grade = ((rows["grade"].get("candidates") or {}).get(slot) or {}).get("grade")
        if final_grade not in GRADES:
            raise DecisionError(f"Agent {slot.upper()} gave no grade to accept.")

    final_value = body.get("final_value")
    if decision == "override_value":
        if final_value is None or isinstance(final_value, bool) or not isinstance(final_value, (int, float)):
            raise DecisionError("Setting the correct value needs the value, as a number.")
        if len(cited) != 1:
            raise DecisionError("A set value applies to exactly one figure; record one decision per figure.")
        final_value = float(final_value)
    elif final_value is not None:
        raise DecisionError("Only \"Set the correct value\" takes a value.")

    return {
        "decision": decision,
        "decided_by": name,
        "rationale": rationale,
        "cited_items": cited,
        "final_value": final_value,
        "final_grade": final_grade,
    }


def gate_state(payload: dict[str, Any], decisions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """
    The gate as a reader needs it. `decisions` in the order they were recorded
    (oldest first); the returned history is newest first.
    """
    history = list(decisions)
    base = {"policy": payload.get("gate_policy"), "identity_note": IDENTITY_NOTE}
    if payload.get("gate_policy") is None:
        return {**base, "status": NOT_GATED, "items": [], "pending": [], "decisions": history[::-1]}

    rows = gate_items(payload)
    current: dict[str, dict[str, Any]] = {}
    for d in history:  # oldest first, so the last write per figure wins
        for metric in d.get("cited_items") or []:
            current[metric] = d
    superseded = {d["decision_id"] for d in history} - {d["decision_id"] for d in current.values()}

    items = [
        {**r, "decision_id": current[r["metric"]]["decision_id"] if r["metric"] in current else None}
        for r in rows
    ]
    pending = [i["metric"] for i in items if i["decision_id"] is None]
    status = NO_DECISION_NEEDED if not rows else AWAITING_DECISION if pending else DECIDED
    # The grade a human recorded, if any: the only grade investors are ever shown (P1).
    grade_decision = current.get("grade")
    decided_grade = ({"grade": grade_decision.get("final_grade"), "decided_by": grade_decision.get("decided_by"),
                      "decided_at": grade_decision.get("decided_at"), "decision_id": grade_decision.get("decision_id")}
                     if grade_decision and grade_decision.get("final_grade") else None)
    return {
        **base,
        "status": status,
        "items": items,
        "pending": pending,
        "decisions": [{**d, "superseded": d["decision_id"] in superseded} for d in history[::-1]],
        "decided_grade": decided_grade,
    }


# ── What an investor-scope reader may see ───────────────────────────────────────

# SEC-01's internal tier, as ReasoningObject.to_dict(investor_scope=True) omits it.
# Keep in step with that method: the B4 assessment keys were missing here on
# 2026-09-26, so stored runs read at investor scope carried the agent's grade.
_INTERNAL_KEYS = ("thought_log", "raw_output", "llm_tokens", "directive_text", "context_window",
                  "assessment", "assessment_status", "assessment_issues", "assessment_source")

# General on purpose: since B3/B5 the open item may be a failed check or the grade, not only a figure.
PENDING_NOTE = "Pending human review: this run has an open decision (a disputed figure, a failed check or the grade)."


def _strip_objects(objects: list[dict[str, Any]], *, withhold_conclusions: bool) -> list[dict[str, Any]]:
    out = []
    for ro in objects or []:
        ro = {k: v for k, v in ro.items() if k not in _INTERNAL_KEYS}
        if withhold_conclusions:
            ro.pop("conclusion", None)
            ro.pop("reasoning_steps", None)
            ro.pop("citations", None)
        out.append(ro)
    return out


_AGENT_PHASES = ("agent_a", "agent_b", "chat")


def strip_search_content(step: dict[str, Any]) -> dict[str, Any]:
    """
    A trace step without anything a search returned or a model wrote: a tool step
    keeps its query and URLs but loses the result preview in `detail` and the
    per-result titles and snippets; an agent step's error keeps only its exception
    type (parse-failure messages can quote model output). Found 2026-09-26: the
    investor read of gated run ec1a3b44 still carried "1998" — agent A's own
    disputed answer — inside a Nintendo search result in step 5's detail. Returns a copy.
    """
    s = dict(step)
    if s.get("kind") == "tool":
        s["detail"] = f"query: {s['query']!r}" if s.get("query") else None
        s.pop("results", None)
        s["result_withheld"] = True
    if s.get("phase") in _AGENT_PHASES and s.get("error"):
        s["error"] = str(s["error"]).split(":", 1)[0]
    return s


def redact_for_scope(payload: dict[str, Any], scope: str, gate: dict[str, Any] | None) -> dict[str, Any]:
    """
    A stored run as `scope` may read it. Auditors see everything. Investors never see
    SEC-01's internal tier; while the gate awaits a decision they also get no
    conclusion, no claims, no value for a contested figure and no search-result text
    in the trace (strip_search_content) — each announced by a marker, never an
    empty box. Returns a copy.
    """
    out = copy.deepcopy(payload)
    if gate is not None:
        out["gate"] = gate
    if scope != "investor":
        return out

    pending = bool(gate) and gate.get("status") == AWAITING_DECISION
    out["reasoning_objects"] = _strip_objects(out.get("reasoning_objects") or [], withhold_conclusions=pending)
    if isinstance(out.get("session"), dict):
        out["session"]["reasoning_objects"] = _strip_objects(
            out["session"].get("reasoning_objects") or [], withhold_conclusions=pending)
        out["session"].pop("directive_text", None)
    out.pop("thought_log", None)
    cmp_ = out.get("cross_agent_comparison")
    if isinstance(cmp_, dict) and isinstance(cmp_.get("synthesis"), dict):
        # Grades, directions and assumptions are model judgments (P8, internal tier):
        # an investor sees what kind of disagreement it is, and a grade only once a
        # named human has recorded one (gate["decided_grade"]).
        syn = cmp_["synthesis"]
        cmp_["synthesis"] = {"policy": syn.get("policy"), "primary_conflict_driver": syn.get("primary_conflict_driver"),
                             "data_conflicts": syn.get("data_conflicts"), "withheld": True}
    for item in (out.get("gate") or {}).get("items") or []:
        if item.get("kind") == "grade":
            item.pop("candidates", None)

    if not pending:
        return out

    pending = set(gate.get("pending") or [])
    pending_items = [i for i in gate.get("items") or [] if i["metric"] in pending]
    # A pending figure item withholds its row; a pending check withholds the rows of
    # the figures it is about, and its own arithmetic (which states them).
    contested = {m for i in pending_items for m in (i.get("metrics") or [i["metric"]])}
    pending_checks = {i["metric"] for i in pending_items if i.get("kind") == "check"}
    for item in out["gate"]["items"]:
        if item["metric"] in pending_checks:
            item["math"] = None
            item["withheld"] = True
    cmp = out.get("cross_agent_comparison")
    if isinstance(cmp, dict):
        for check in (cmp.get("structural_flags") or {}).get("checks") or []:
            # Checks on the filing itself show filed values, not an agent's claim: kept.
            agent_claim = check.get("scope") != "source"
            if check.get("key") in pending_checks or (agent_claim and set(check.get("metrics") or []) & contested):
                check["math"] = None
                check["withheld"] = True
        cmp["agent_a_conclusion"] = None
        cmp["agent_b_conclusion"] = None
        cmp["agent_a_numbers"] = []
        cmp["agent_b_numbers"] = []
        cmp["divergent_numbers"] = []
        for row in cmp.get("metric_comparisons") or []:
            if row.get("metric") in contested:
                row.update(value_a=None, value_b=None, raw_a=None, raw_b=None, variance_pct=None,
                           withheld=True)
    if "claims" in out:
        out["claims"] = {"a": [], "b": []} if isinstance(out["claims"], dict) else []
    if isinstance(out.get("steps"), list):
        out["steps"] = [strip_search_content(s) for s in out["steps"]]
    out["withheld_pending_review"] = PENDING_NOTE
    return out
