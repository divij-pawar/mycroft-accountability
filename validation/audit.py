"""
B6 — the audit record of one compare run, as JSON (for machines) and Markdown (for
people; AGENTS.md: "default to Markdown for humans").

Shape: the pasted three-tier design's audit payload — metric_comparisons,
structural_flags, primary_conflict_driver, grade_candidates, consensus_grade and its
reason, audit_recommendation, gate status and the decisions — plus where every
filing figure came from. Nothing is recomputed here: every field is read from what
the run already recorded (P3), and the stored record itself is not changed; this is
a view of it. Built from the run AFTER validation/gate.py's redact_for_scope, so an
investor's export can never carry more than an investor's read of the run does.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

AUDIT_FORMAT = "audit-v1"

_DRIVER = {
    "data": "the figures conflict",
    "assumption": "a stated assumption differs",
    "weighting": "the same evidence is weighed differently",
    "none": "no disagreement in grade or direction",
    "insufficient": "there is no pair of grades to compare",
}
_STATUS = {
    "MATCH": "Match", "MISMATCH": "MISMATCH", "DIFFERENT_PERIODS": "Different periods",
    "UNVERIFIABLE_PERIOD": "Period stated by one agent only", "ONE_SIDED": "Only one agent was given this",
    "CITED_BY_ONE": "Both given it; one cited it", "UNCORROBORATED": "Only one agent cited this, nothing backs it",
    "DERIVED_OK": "Recomputed, checks out", "DERIVED_WRONG": "Doesn't add up",
}


def _filings(run: dict) -> list[dict[str, Any]]:
    """One row per filing the agents' figures came from (from the recorded facts)."""
    cik = next((s.get("cik") for s in run.get("steps") or [] if s.get("cik")), None)
    seen: dict[str, dict[str, Any]] = {}
    for slot in ("a", "b"):
        for f in (run.get("facts") or {}).get(slot) or []:
            accn = f.get("accn")
            if not accn or f.get("missing"):
                continue
            row = seen.setdefault(accn, {"accn": accn, "form": f.get("form"), "filed": f.get("filed"), "concepts": [],
                                         "url": (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/"
                                                 f"{accn}-index.htm") if cik else None})
            if f.get("concept") not in row["concepts"]:
                row["concepts"].append(f.get("concept"))
    return list(seen.values())


def audit_record(run: dict, *, scope: str) -> dict[str, Any]:
    """The audit payload for a run already redacted for `scope` (web/server.py does that)."""
    cmp = run.get("cross_agent_comparison") or {}
    syn = cmp.get("synthesis") or {}
    gate = run.get("gate") or {}
    session = run.get("session") or {}
    return {
        "format": AUDIT_FORMAT,
        "run_id": run.get("run_id"),
        "subject": run.get("ticker") or run.get("subject"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": scope,
        "directive_version": session.get("directive_version"),
        "producers": run.get("producers"),
        "comparison_status": cmp.get("status"),
        "contradiction_flag": cmp.get("contradiction_flag"),
        "contradiction_rule": cmp.get("contradiction_rule"),
        "metric_comparisons": cmp.get("metric_comparisons"),
        "structural_flags": cmp.get("structural_flags"),
        "primary_conflict_driver": syn.get("primary_conflict_driver"),
        "grade_candidates": syn.get("grade_candidates"),
        "consensus_grade": syn.get("consensus_grade"),
        "consensus_reason": syn.get("consensus_reason"),
        "audit_recommendation": syn.get("audit_recommendation"),
        "grades_withheld": bool(syn.get("withheld")),
        "gate_status": gate.get("status"),
        "gate_pending": gate.get("pending"),
        "decided_grade": gate.get("decided_grade"),
        "decisions": gate.get("decisions"),
        "decider_identity": gate.get("identity_note"),
        "filings": _filings(run),
        "withheld_pending_review": run.get("withheld_pending_review"),
    }


# ── Markdown ───────────────────────────────────────────────────────────────────

def _cell(v: Any) -> str:
    # Inside a table cell: escape the column separator, keep it on one line.
    return "—" if v in (None, "") else str(v).replace("|", "\\|").replace("\n", " ")


def _value(row: dict, side: str) -> str:
    if row.get("withheld"):
        return "withheld"
    raw = row.get(f"raw_{side}")
    period = row.get(f"period_{side}")
    return _cell(raw) + (f" ({period})" if raw and period else "")


def to_markdown(a: dict[str, Any]) -> str:
    """A reviewer's report of the audit record. Every line is from a recorded field."""
    out: list[str] = []
    w = out.append
    w(f"# Review: {a['subject']} — run `{a['run_id']}`")
    w("")
    w(f"Generated {a['generated_at']} at **{a['scope']}** scope · directive {a.get('directive_version') or '—'} · "
      f"format {a['format']}. Figures and checks are machine-verified conformance; whether the run is adequate is a "
      "human judgment.")
    w("")
    if a.get("withheld_pending_review"):
        w(f"> **{a['withheld_pending_review']}** Disputed figures and both conclusions are withheld from this export.")
        w("")

    w("## Summary")
    w("")
    w(f"- Comparison: {a.get('comparison_status') or '—'}; recorded verdict "
      f"{'flagged for review' if a.get('contradiction_flag') else 'not flagged' if a.get('contradiction_flag') is False else 'not compared'}"
      f" ({a.get('contradiction_rule') or '—'}).")
    if a.get("primary_conflict_driver"):
        w(f"- Why the views differ: {_DRIVER.get(a['primary_conflict_driver'], a['primary_conflict_driver'])}.")
    if a.get("audit_recommendation"):
        w(f"- Recommendation: {a['audit_recommendation']}")
    if a.get("decided_grade"):
        d = a["decided_grade"]
        w(f"- **Grade: {d['grade']}**, set by {d['decided_by']} on {d['decided_at']} (a named human; identity self-declared).")
    elif a.get("grades_withheld"):
        w("- Grade: none published. The agents' grades are model judgments and stay internal until a reviewer sets one.")
    w(f"- Decision gate: {a.get('gate_status') or '—'}"
      + (f" — still open: {', '.join(a['gate_pending'])}" if a.get("gate_pending") else "") + ".")
    w("")

    rows = a.get("metric_comparisons") or []
    if rows:
        w("## Figures, agent by agent")
        w("")
        w("| Figure | Agent A | Agent B | Difference | Result |")
        w("|---|---|---|---|---|")
        for r in rows:
            diff = f"{r['variance_pct']}%" if r.get("variance_pct") not in (None, 0) and not r.get("withheld") else "—"
            w(f"| {_cell(r.get('label'))} | {_value(r, 'a')} | {_value(r, 'b')} | {diff} | {_STATUS.get(r.get('status'), r.get('status'))} |")
        w("")

    checks = (a.get("structural_flags") or {}).get("checks") or []
    if checks:
        w("## Accounting checks")
        w("")
        for c in checks:
            mark = {"pass": "✓", "fail": "≠" if c.get("kind") == "hard" else "⚠", "skipped": "—"}.get(c.get("outcome"), "?")
            math = "withheld" if c.get("withheld") else (c.get("math") or "")
            note = " — needs a decision" if c.get("gates") else (" — unusual, not an error on its own"
                                                                 if c.get("kind") == "heuristic" and c.get("outcome") == "fail" else "")
            w(f"- {mark} **{c.get('who')}**: {c.get('plain')}{note}" + (f" · `{math}`" if math else "")
              + (f" · {c['reason']}" if c.get("reason") and c.get("outcome") != "pass" else ""))
        w("")

    cands = a.get("grade_candidates") or []
    if cands and any(c.get("grade") for c in cands):
        w("## Grade candidates (model judgments)")
        w("")
        w("| Agent | Grade | Direction | Match filing | Contradict | Unchecked | Unbacked | Failed hard checks |")
        w("|---|---|---|---|---|---|---|---|")
        for c in cands:
            e = c.get("evidence") or {}
            w(f"| {c['slot'].upper()} | {_cell(c.get('grade'))} | {_cell(c.get('direction'))} | {e.get('match_filing', 0)} | "
              f"{e.get('contradict_filing', 0)} | {e.get('unchecked', 0)} | {e.get('unbacked', 0)} | {e.get('hard_check_failures', 0)} |")
        if a.get("consensus_grade"):
            cg = a["consensus_grade"]
            w("")
            w(f"Consensus proposed: **{cg['grade']}, {cg['direction']}** — not a published grade until a reviewer sets it.")
        elif a.get("consensus_reason"):
            w("")
            w(f"No consensus: {a['consensus_reason']}")
        w("")

    decisions = a.get("decisions") or []
    if decisions:
        w("## Decisions (append-only; newest first)")
        w("")
        for d in decisions:
            what = d.get("decision")
            extra = f" → {d['final_grade']}" if d.get("final_grade") else f" → {d['final_value']}" if d.get("final_value") is not None else ""
            w(f"- {d.get('decided_at')} · **{d.get('decided_by')}** · {what}{extra} on {', '.join(d.get('cited_items') or [])}"
              + (" · *superseded*" if d.get("superseded") else ""))
            w(f"  > {d.get('rationale')}")
        if a.get("decider_identity"):
            w("")
            w(f"*{a['decider_identity']}*")
        w("")

    if a.get("filings"):
        w("## Sources")
        w("")
        for f in a["filings"]:
            w(f"- {f.get('form') or 'Filing'} filed {f.get('filed') or '—'}, accession {f['accn']}"
              + (f" — {f['url']}" if f.get("url") else "") + f" (figures: {', '.join(f['concepts'])})")
        w("")
    return "\n".join(out).rstrip() + "\n"
