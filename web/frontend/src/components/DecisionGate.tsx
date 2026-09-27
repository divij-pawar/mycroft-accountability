import { useEffect, useId, useState } from "react";
import { api } from "../api/client";
import type { DecisionInput, DecisionKind, Gate, GateDecision, GateItem, MetricComparison, Scope } from "../api/types";
import { formatFigure } from "../lib/format";
import { Info, RolePill, StatusBadge } from "./primitives";

// U3 — the inline decision gate (P1: humans decide; P4: gates are hard stops a named
// human clears). It sits directly under the comparison headline and expands in
// place — never a modal — so the figures it is about stay readable while the
// reviewer writes. The rules it enforces are the server's (validation/gate.py);
// the form only mirrors them so a reviewer isn't surprised by a refusal.

export const DECISION_LABEL: Record<DecisionKind, string> = {
  accept_a: "Agent A's figure is right",
  accept_b: "Agent B's figure is right",
  both_wrong: "Both figures are wrong",
  not_a_conflict: "Not a real conflict",
  override_value: "Set the correct value",
  confirmed_error: "The check is right: the agent's figure is wrong",
  set_grade: "Set the grade",
};

// For a grade item the same decisions read differently (B5): accepting A means A's grade.
const GRADE_LABEL: Partial<Record<DecisionKind, string>> = {
  accept_a: "Agent A's grade is right",
  accept_b: "Agent B's grade is right",
};

export const GRADES = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC"] as const;

const DECISION_HINT: Record<DecisionKind, string> = {
  accept_a: "You checked the source and A matches it.",
  accept_b: "You checked the source and B matches it.",
  both_wrong: "Neither value matches the source.",
  not_a_conflict: "Nothing is actually wrong, e.g. a different but valid definition or period.",
  override_value: "Record the value the source actually gives.",
  confirmed_error: "You looked, and the agent's figure really is wrong.",
  set_grade: "Record the grade the evidence supports, whichever agent it matches.",
};

// Mirrors validation/gate.py DECISIONS_FOR: which decisions fit which kind of item.
const DECISIONS_FOR: Record<"figure" | "check" | "grade", DecisionKind[]> = {
  figure: ["accept_a", "accept_b", "both_wrong", "not_a_conflict", "override_value"],
  check: ["confirmed_error", "not_a_conflict", "override_value"],
  grade: ["accept_a", "accept_b", "set_grade"],
};

const kindOf = (item: GateItem | undefined) => item?.kind ?? "figure";

/** Decisions that apply to every cited item (all of them if nothing is picked yet). */
function allowedDecisions(items: GateItem[], cited: string[]): DecisionKind[] {
  const kinds = new Set(items.filter((i) => cited.includes(i.metric)).map(kindOf));
  const order = (Object.keys(DECISION_LABEL) as DecisionKind[]);
  if (!kinds.size) return order.filter((k) => items.some((i) => DECISIONS_FOR[kindOf(i)].includes(k)));
  return order.filter((k) => [...kinds].every((kind) => DECISIONS_FOR[kind].includes(k)));
}

export const MIN_RATIONALE = 20;
const MIN_NAME = 2;

interface Draft {
  decision: DecisionKind | null;
  cited: string[];
  value: string;
  grade: string;
  rationale: string;
  name: string;
}

const draftKey = (runId: string) => `gate-draft:${runId}`;

function loadDraft(runId: string, pending: string[]): Draft {
  try {
    const raw = sessionStorage.getItem(draftKey(runId));
    if (raw) return { grade: "", ...(JSON.parse(raw) as Partial<Draft>) } as Draft;
  } catch { /* storage unavailable or corrupt: start fresh */ }
  return { decision: null, cited: pending, value: "", grade: "", rationale: "", name: "" };
}

const isDirty = (d: Draft) => Boolean(d.decision || d.rationale.trim() || d.value.trim() || d.grade);

function problems(d: Draft, allowed: DecisionKind[]): string[] {
  const out: string[] = [];
  if (!d.cited.length) out.push("Pick at least one item");
  if (!d.decision) out.push("Choose a decision");
  else if (!allowed.includes(d.decision)) out.push("That decision doesn't fit the items picked");
  if (d.decision === "override_value") {
    if (d.cited.length !== 1) out.push("A set value applies to exactly one figure");
    if (d.value.trim() === "" || !Number.isFinite(Number(d.value))) out.push("Enter the value as a number");
  }
  if (d.decision === "set_grade" && !(GRADES as readonly string[]).includes(d.grade)) out.push("Choose the grade");
  if (d.rationale.trim().length < MIN_RATIONALE) out.push(`Write at least ${MIN_RATIONALE} characters of reasoning`);
  if (d.name.trim().length < MIN_NAME) out.push("Enter your name");
  return out;
}

function valueOf(row: MetricComparison | undefined, side: "a" | "b"): string {
  if (!row) return "—";
  const v = side === "a" ? row.value_a : row.value_b;
  const raw = side === "a" ? row.raw_a : row.raw_b;
  if (v === null || raw === null) return "—";
  return row.family === "currency" ? formatFigure(v, "USD")
       : row.family === "per_share" ? formatFigure(v, "USD/shares") : raw;
}

/** Says what kind of problem is open: disputed figures, failed checks, or both. */
function gateTitle(gate: Gate, awaiting: boolean): string {
  const n = (k: number, one: string, many: string) => (k === 1 ? `1 ${one}` : `${k} ${many}`);
  const figures = gate.items.filter((i) => kindOf(i) === "figure").length;
  const grades = gate.items.filter((i) => kindOf(i) === "grade").length;
  const checks = gate.items.length - figures - grades;
  if (grades) {
    if (!awaiting) return "A reviewer has decided every item, including the grade";
    if (gate.items.length === 1) return "The agents' grades differ: a reviewer must set the grade";
    return `${gate.pending.length} of ${gate.items.length} items need a decision, including the grade`;
  }
  if (!awaiting) return checks ? "A reviewer has decided every item" : "A reviewer has decided every disputed figure";
  if (gate.pending.length < gate.items.length) {
    return `${gate.pending.length} of ${gate.items.length} items still need a decision`;
  }
  if (!checks) {
    return `The agents disagree on ${figures === 1 ? "a figure" : `${figures} figures`}: a reviewer must decide which is right`;
  }
  if (!figures) return `${n(checks, "accounting check", "accounting checks")} failed: a reviewer must decide`;
  return `${n(figures, "disputed figure", "disputed figures")} and ${n(checks, "failed check", "failed checks")}: a reviewer must decide`;
}

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function DecisionRecord({ d, labels }: { d: GateDecision; labels: Record<string, string> }) {
  return (
    <li className={`decision-record ${d.superseded ? "superseded" : ""}`}>
      <div className="decision-line">
        <strong>{d.cited_items.includes("grade") ? GRADE_LABEL[d.decision] ?? DECISION_LABEL[d.decision] : DECISION_LABEL[d.decision]}</strong>
        {d.final_value !== null && <span className="figure">: {d.final_value}</span>}
        {d.final_grade && <span>: grade <span className="grade-badge">{d.final_grade}</span></span>}
        <span className="muted small"> · {d.cited_items.map((m) => labels[m] ?? m).join(", ")}</span>
        {d.superseded && <span className="badge-neutral">Superseded</span>}
      </div>
      <div className="muted small">Decided by <strong>{d.decided_by}</strong> · {when(d.decided_at)}</div>
      <p className="decision-rationale">{d.rationale}</p>
    </li>
  );
}

export function DecisionGate({ runId, gate, rows, scope, onDecided }: {
  runId: string;
  gate: Gate;
  rows: MetricComparison[];
  scope: Scope;
  onDecided?: (gate: Gate) => void;
}) {
  const formId = useId();
  const byMetric = Object.fromEntries(rows.map((r) => [r.metric, r]));
  const labels = Object.fromEntries(gate.items.map((i) => [i.metric, i.label]));
  const awaiting = gate.status === "AWAITING_DECISION";
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => loadDraft(runId, gate.pending));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keep a half-written decision across reloads and accidental navigation.
  useEffect(() => {
    try {
      if (isDirty(draft)) sessionStorage.setItem(draftKey(runId), JSON.stringify(draft));
      else sessionStorage.removeItem(draftKey(runId));
    } catch { /* storage unavailable: the draft just won't survive a reload */ }
    if (!isDirty(draft)) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [draft, runId]);

  if (gate.status === "NOT_GATED" || gate.status === "NO_DECISION_NEEDED") return null;

  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));
  const toggleCited = (metric: string) =>
    set({ cited: draft.cited.includes(metric) ? draft.cited.filter((m) => m !== metric) : [...draft.cited, metric] });
  const allowed = allowedDecisions(gate.items, draft.cited);
  const citedKinds = new Set(gate.items.filter((i) => draft.cited.includes(i.metric)).map(kindOf));
  const onlyGrades = citedKinds.size === 1 && citedKinds.has("grade");
  const issues = problems(draft, allowed);
  const current = gate.decisions.filter((d) => !d.superseded);
  const superseded = gate.decisions.filter((d) => d.superseded);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (issues.length || !draft.decision) return;
    setSaving(true);
    setError(null);
    const body: DecisionInput = {
      decision: draft.decision,
      decided_by: draft.name.trim(),
      rationale: draft.rationale.trim(),
      cited_items: draft.cited,
      final_value: draft.decision === "override_value" ? Number(draft.value) : null,
      final_grade: draft.decision === "set_grade" ? draft.grade : null,
    };
    try {
      const next = await api.addDecision(scope, runId, body);
      setDraft({ decision: null, cited: next.pending, value: "", grade: "", rationale: "", name: draft.name });
      setOpen(false);
      onDecided?.(next);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className={`gate ${awaiting ? "gate-awaiting" : "gate-decided"}`} aria-labelledby={`${formId}-title`}>
      <div className="gate-head">
        <RolePill role="user" />
        <StatusBadge state={awaiting ? "awaiting_decision" : "decided"} />
        <h3 id={`${formId}-title`} className="gate-title">
          {gateTitle(gate, awaiting)}
        </h3>
        <Info term="gate" />
      </div>

      {awaiting && (
        <p className="muted small gate-sub">
          Until then, investors see "pending human review" instead of these figures and the agents' conclusions.
          Still open: {gate.pending.map((m) => labels[m] ?? m).join(", ")}.
        </p>
      )}

      {current.length > 0 && (
        <ul className="plain-list decision-list" aria-label="Decisions in effect">
          {current.map((d) => <DecisionRecord key={d.decision_id} d={d} labels={labels} />)}
        </ul>
      )}
      {superseded.length > 0 && (
        <details className="decision-history">
          <summary>Earlier decisions, since superseded ({superseded.length})</summary>
          <ul className="plain-list">{superseded.map((d) => <DecisionRecord key={d.decision_id} d={d} labels={labels} />)}</ul>
        </details>
      )}

      {scope !== "auditor" ? (
        awaiting && <p className="scope-notice">Pending human review. An auditor records the decision.</p>
      ) : (
        <>
          <button type="button" className={awaiting ? "btn" : "btn-ghost"} aria-expanded={open}
                  aria-controls={`${formId}-form`} onClick={() => setOpen((o) => !o)}>
            {open ? "Close without recording" : awaiting ? "Review and record decision" : "Record a new decision"}
            {!open && isDirty(draft) && <span className="btn-note"> (draft saved)</span>}
          </button>
          {open && (
            <form id={`${formId}-form`} className="gate-form" onSubmit={submit} noValidate>
              <fieldset>
                <legend>Which items is this decision about?</legend>
                {gate.items.map((item) => (
                  <label key={item.metric} className="gate-item">
                    <input type="checkbox" checked={draft.cited.includes(item.metric)}
                           onChange={() => toggleCited(item.metric)} />
                    <span className="gate-item-label">{item.label}</span>
                    {kindOf(item) === "grade" ? (
                      <span className="gate-values">
                        {(["a", "b"] as const).map((s) => {
                          const c = item.candidates?.[s];
                          return (
                            <span key={s} className={`lane-tag lane-tag-${s}`}>
                              {s.toUpperCase()} {c?.grade ?? "—"}{c?.direction ? ` · ${c.direction}` : ""}
                              {c && Object.keys(c.assumptions ?? {}).length > 0 &&
                                ` · ${Object.entries(c.assumptions).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ")}`}
                            </span>
                          );
                        })}
                      </span>
                    ) : kindOf(item) === "check" ? (
                      <span className="gate-values">
                        <code className="check-math">{item.math ?? "withheld"}</code>
                      </span>
                    ) : (
                      <span className="gate-values">
                        <span className="lane-tag lane-tag-a">A {valueOf(byMetric[item.metric], "a")}</span>
                        <span className="lane-tag lane-tag-b">B {valueOf(byMetric[item.metric], "b")}</span>
                      </span>
                    )}
                    {item.decision_id
                      ? <StatusBadge state="decided" />
                      : <StatusBadge state="awaiting_decision">Open</StatusBadge>}
                  </label>
                ))}
              </fieldset>

              <fieldset>
                <legend>Your decision</legend>
                <div className="radio-cards">
                  {allowed.map((k) => (
                    <label key={k} className={`radio-card ${draft.decision === k ? "checked" : ""}`}>
                      <input type="radio" name={`${formId}-decision`} value={k} checked={draft.decision === k}
                             onChange={() => set({ decision: k })} />
                      <span className="radio-title">{onlyGrades ? GRADE_LABEL[k] ?? DECISION_LABEL[k] : DECISION_LABEL[k]}</span>
                      <span className="muted small">{DECISION_HINT[k]}</span>
                    </label>
                  ))}
                </div>
                {draft.decision === "set_grade" && (
                  <label className="gate-field">
                    Grade
                    <select value={draft.grade} onChange={(e) => set({ grade: e.target.value })}>
                      <option value="">Choose…</option>
                      {GRADES.map((g) => <option key={g} value={g}>{g}</option>)}
                    </select>
                  </label>
                )}
                {draft.decision === "override_value" && (
                  <label className="gate-field">
                    Correct value, as a plain number (e.g. 94930000000 or 1.57)
                    <input inputMode="decimal" value={draft.value} onChange={(e) => set({ value: e.target.value })} />
                  </label>
                )}
              </fieldset>

              <label className="gate-field">
                Why? What did you check?
                <textarea rows={3} value={draft.rationale} onChange={(e) => set({ rationale: e.target.value })}
                          aria-describedby={`${formId}-count`}
                          placeholder="e.g. The 10-Q income statement gives $94.93B for the quarter; B used the 9-month figure." />
                <span id={`${formId}-count`} className={`muted small ${draft.rationale.trim().length >= MIN_RATIONALE ? "" : "count-short"}`}>
                  {draft.rationale.trim().length} / {MIN_RATIONALE} characters minimum
                </span>
              </label>

              <label className="gate-field">
                Your name
                <input value={draft.name} onChange={(e) => set({ name: e.target.value })} autoComplete="name" />
                <span className="muted small">Recorded as entered; not verified.</span>
              </label>

              {issues.length > 0 && (
                <p className="muted small" id={`${formId}-issues`}>To record this: {issues.join(" · ")}.</p>
              )}
              <div className="gate-actions">
                <button type="submit" className="btn" disabled={saving || issues.length > 0}
                        aria-describedby={issues.length ? `${formId}-issues` : undefined}>
                  {saving ? "Recording…" : "Record decision"}
                </button>
                <span className="muted small">Decisions can't be edited or deleted; a later one supersedes it.</span>
              </div>
              {error && <p className="error-text" role="alert">{error}</p>}
            </form>
          )}
        </>
      )}
    </section>
  );
}
