import type { Gate, Synthesis } from "../api/types";
import { StatusBadge } from "./primitives";

// U8 — the grade question, answered in the review summary (B5). What kind of
// disagreement it is comes first, in words the reviewer can act on; then each
// agent's grade beside the evidence counted behind it — counts, no scores or gauges
// (P3). Grades are model judgments (P8) and are labelled so; the one grade an
// investor can ever see is the one a named human recorded through the gate.

const DRIVER: Record<string, string> = {
  data: "They disagree because the figures conflict",
  assumption: "They disagree because of an assumption",
  weighting: "They disagree on how to weigh the same evidence",
  none: "Both agents give the same grade",
  insufficient: "There is no pair of grades to compare",
};

const ARROW: Record<string, string> = { buy: "↑", hold: "→", sell: "↓" };

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function DecidedGrade({ decided }: { decided: NonNullable<Gate["decided_grade"]> }) {
  return (
    <p className="decided-grade">
      <StatusBadge state="decided">Grade set by a reviewer</StatusBadge>{" "}
      <span className="grade-badge">{decided.grade}</span>{" "}
      <span className="muted small">by {decided.decided_by} · {when(decided.decided_at)}</span>
    </p>
  );
}

export function GradesPanel({ synthesis, decided }: { synthesis: Synthesis; decided?: Gate["decided_grade"] }) {
  const driver = DRIVER[synthesis.primary_conflict_driver] ?? synthesis.primary_conflict_driver;

  if (synthesis.withheld) {
    // Investor scope: the kind of disagreement, and a grade only if a human set one.
    return (
      <div className="grades" aria-label="Grade">
        {decided ? <DecidedGrade decided={decided} /> : (
          <p className="scope-notice small">
            The agents' grades are model judgments and stay internal until a reviewer sets the grade.
          </p>
        )}
        {synthesis.primary_conflict_driver !== "insufficient" && <p className="small muted">{driver}.</p>}
      </div>
    );
  }

  const cands = synthesis.grade_candidates ?? [];
  const graded = cands.some((c) => c.grade);
  return (
    <div className="grades" aria-label="Grade">
      {decided && <DecidedGrade decided={decided} />}
      <p className="grades-driver"><strong>{driver}.</strong> {synthesis.audit_recommendation}</p>
      {graded && (
        <div className="grade-cands">
          {cands.map((c) => (
            <section key={c.slot} className={`grade-cand lane-${c.slot}`} aria-label={`Agent ${c.slot.toUpperCase()}'s grade`}>
              <div className="grade-cand-head">
                <strong>Agent {c.slot.toUpperCase()}</strong>
                {c.grade ? <span className="grade-badge">{c.grade}</span> : <span className="muted small">no grade</span>}
                {c.direction && <span className="badge-neutral"><span aria-hidden="true">{ARROW[c.direction]}</span> {c.direction}</span>}
              </div>
              {Object.keys(c.assumptions).length > 0 && (
                <p className="small">
                  Assumes {Object.entries(c.assumptions).map(([k, v]) =>
                    k === "revenue_growth_pct" ? `${v}% revenue growth` : k === "horizon_months" ? `a ${v}-month horizon` : `${v} margins`).join(", ")}
                </p>
              )}
              <ul className="evidence-counts small" aria-label="Evidence behind this grade">
                <li><span className="figure">{c.evidence.match_filing}</span> figures match the filing</li>
                <li><span className="figure">{c.evidence.contradict_filing}</span> contradict it</li>
                <li><span className="figure">{c.evidence.unchecked}</span> unchecked</li>
                <li><span className="figure">{c.evidence.unbacked}</span> with nothing to back them</li>
                <li><span className="figure">{c.evidence.hard_check_failures}</span> failed hard checks</li>
              </ul>
            </section>
          ))}
        </div>
      )}
      {graded && <p className="muted small">Grades are the agents' own judgments, read from their answers (model judgment).</p>}
      {synthesis.consensus_grade ? (
        <p className="small">
          <StatusBadge state="match">Consensus</StatusBadge>{" "}
          <span className="grade-badge">{synthesis.consensus_grade.grade}</span> {synthesis.consensus_grade.direction}
          <span className="muted"> — both agents, no failed hard checks. Investors see no grade until a reviewer sets one.</span>
        </p>
      ) : synthesis.needs_decision ? (
        <p className="small"><StatusBadge state="awaiting_decision">No consensus: needs your decision</StatusBadge></p>
      ) : graded && synthesis.consensus_reason ? (
        <p className="muted small">No consensus: {synthesis.consensus_reason}</p>
      ) : null}
    </div>
  );
}
