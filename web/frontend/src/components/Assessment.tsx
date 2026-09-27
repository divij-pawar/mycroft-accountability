import { useState } from "react";
import type { MetricComparison, ReasoningObject } from "../api/types";
import { StatusBadge } from "./primitives";

// U7 — an agent's structured <assessment> (B4, directive v1.6.0+), where the agent is.
// The grade and direction are the model's judgment (P8), labelled as such. When a
// block was asked for and came back broken, the reviewer can see why in place: the
// raw output opens inline with the region where the block was expected marked, so a
// dropped tag can be told from a mangled one without leaving the page.

export type Assessment = {
  grade?: string;
  direction?: "buy" | "hold" | "sell";
  assumptions?: { revenue_growth_pct?: number; margin_trend?: string; horizon_months?: number };
  key_metrics?: string[];
  key_points?: string[];
};

const ARROW: Record<string, string> = { buy: "↑", hold: "→", sell: "↓" };
const ASSUMPTION_LABEL: Record<string, string> = {
  revenue_growth_pct: "Revenue growth", margin_trend: "Margin trend", horizon_months: "Horizon",
};

const BROKEN = new Set(["invalid_json", "unclosed", "empty", "invalid_fields"]);

/** Directive v1.6.0 or later — the versions that ask for the block (core.assessment.expects_assessment). */
export function asksForAssessment(version: string | undefined): boolean {
  const m = version?.match(/^v(\d+)\.(\d+)\.(\d+)$/);
  if (!m) return false;
  const [maj, min] = [Number(m[1]), Number(m[2])];
  return maj > 1 || (maj === 1 && min >= 6);
}

/** The attempt whose answer counts for this agent: its successful one, if any. */
export function finalAttempt(objects: ReasoningObject[], agentId: string | undefined): ReasoningObject | undefined {
  const mine = objects.filter((o) => !agentId || o.agent_id === agentId);
  return mine.find((o) => o.parse_status === "SUCCESS") ?? mine[mine.length - 1];
}

function assumptionValue(key: string, v: unknown): string {
  if (key === "revenue_growth_pct") return `${v}% a year`;
  if (key === "horizon_months") return `${v} months`;
  return String(v);
}

/** Where the <assessment> block sits in the raw text, or where it was expected. */
function assessmentRegion(raw: string): { start: number; end: number; found: boolean } {
  const open = raw.indexOf("<assessment>");
  if (open >= 0) {
    const close = raw.indexOf("</assessment>", open);
    return { start: open, end: close >= 0 ? close + "</assessment>".length : raw.length, found: true };
  }
  const after = raw.lastIndexOf("</conclusion>");
  const at = after >= 0 ? after + "</conclusion>".length : raw.length;
  return { start: at, end: at, found: false };
}

export function RawOutput({ raw }: { raw: string }) {
  const r = assessmentRegion(raw);
  return (
    <pre className="prompt-text raw-output">
      {raw.slice(0, r.start)}
      {r.found
        ? <mark className="raw-region"><span className="sr-only">assessment block: </span>{raw.slice(r.start, r.end)}</mark>
        : <mark className="raw-missing">{"⟵ no <assessment> block here, after </conclusion>"}</mark>}
      {raw.slice(r.end)}
    </pre>
  );
}

function Strip({ a, issues }: { a: Assessment; issues: string[] }) {
  const assumptions = Object.entries(a.assumptions ?? {});
  return (
    <div className="assessment">
      <div className="assessment-head">
        {a.grade && (
          // Labelled: a bare "A" under lane A reads as the lane, not the grade (seen live).
          <span className="grade" title="The agent's judgment of financial strength, AAA (strongest) to CCC">
            <span className="muted small">Grade</span> <span className="grade-badge">{a.grade}</span>
          </span>
        )}
        {a.direction && (
          <span className="badge-neutral direction"><span aria-hidden="true">{ARROW[a.direction]}</span> {a.direction[0].toUpperCase() + a.direction.slice(1)}</span>
        )}
        <span className="badge-neutral judgment" title="Stated by the model; not a verified fact (P8)">Model judgment</span>
      </div>
      {assumptions.length > 0 && (
        <table className="assumptions">
          <caption className="sr-only">Key assumptions</caption>
          <tbody>
            {assumptions.map(([k, v]) => (
              <tr key={k}><th scope="row">{ASSUMPTION_LABEL[k] ?? k}</th><td>{assumptionValue(k, v)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {!!a.key_points?.length && <ul className="key-points">{a.key_points.map((p) => <li key={p}>{p}</li>)}</ul>}
      {issues.length > 0 && (
        <details className="assessment-issues">
          <summary className="muted small">Some of the block was left out ({issues.length})</summary>
          <ul className="small">{issues.map((i) => <li key={i}>{i}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

/** The assessment for one agent's final attempt, or the right empty state for why there isn't one. */
export function AssessmentView({ ro }: { ro: ReasoningObject | undefined }) {
  const [showRaw, setShowRaw] = useState(false);
  if (!ro) return null;
  if (!("assessment_status" in ro)) {
    // Investor scope omits the key (SEC-01); a run from before v1.6.0 has no status either.
    return asksForAssessment(ro.directive_version) && !("raw_output" in ro)
      ? <p className="scope-notice small">The structured assessment is withheld at investor scope.</p>
      : null;
  }
  const status = ro.assessment_status;
  const issues = ro.assessment_issues ?? [];
  if (status === null || status === undefined) return null; // not asked for (corrective retry)
  if ((status === "valid" || status === "partial") && ro.assessment) {
    return <Strip a={ro.assessment as Assessment} issues={issues} />;
  }
  if (status === "absent") {
    return <p className="muted small">No structured grade: the agent left the optional assessment out.</p>;
  }
  const raw = ro.raw_output?.text;
  return (
    <div className="assessment assessment-empty">
      <p className="small">
        <StatusBadge state="parse_failure">No structured grade</StatusBadge>{" "}
        This agent didn't give a usable one. {BROKEN.has(status) && issues[0]}
      </p>
      {raw && (
        <>
          <button type="button" className="btn-ghost small" aria-expanded={showRaw} onClick={() => setShowRaw((s) => !s)}>
            {showRaw ? "Hide raw output" : "View raw output"}
          </button>
          {showRaw && <RawOutput raw={raw} />}
        </>
      )}
    </div>
  );
}

// ── Mobile Compare Mode ────────────────────────────────────────────────────────

/** Up to three lines that stand for the agent's view — its own key points, else an excerpt. */
export function summaryLines(ro: ReasoningObject | undefined, conclusion: string | null): { lines: string[]; excerpt: boolean } {
  const points = (ro?.assessment as Assessment | undefined)?.key_points;
  if (points?.length) return { lines: points.slice(0, 3), excerpt: false };
  const bullets = (ro?.thought_log ?? "").split("\n").map((l) => l.trim())
    .filter((l) => /^([-*•]|\d+[.)])\s+/.test(l)).map((l) => l.replace(/^([-*•]|\d+[.)])\s+/, ""));
  if (bullets.length) return { lines: bullets.slice(0, 3), excerpt: true };
  const sentences = (conclusion ?? "").match(/[^.!?]+[.!?]+/g) ?? (conclusion ? [conclusion] : []);
  return { lines: sentences.slice(0, 2).map((s) => s.trim()), excerpt: true };
}

export function CompareModeView({ lanes, rows }: {
  lanes: { slot: "a" | "b"; label: string; ro: ReasoningObject | undefined; conclusion: string | null; withheld: boolean }[];
  rows: MetricComparison[] | null | undefined;
}) {
  const disputed = (rows ?? []).filter((r) => r.status === "MISMATCH").map((r) => r.label);
  return (
    <div className="compare-mode" aria-label="Compare mode">
      {disputed.length > 0 && <p className="small compare-disputes"><strong>Disagreements:</strong> {disputed.join(", ")}</p>}
      <div className="compare-cols">
        {lanes.map((l) => {
          const a = l.ro?.assessment as Assessment | undefined;
          const s = summaryLines(l.ro, l.conclusion);
          return (
            <section key={l.slot} className={`compare-col lane-${l.slot}`} aria-label={l.label}>
              <h3 className="compare-col-head">{l.label}</h3>
              {a?.grade || a?.direction ? (
                <p className="small">
                  {a.grade && <><span className="muted">Grade</span> <span className="grade-badge">{a.grade}</span></>}{" "}
                  {a.direction && <span className="badge-neutral"><span aria-hidden="true">{ARROW[a.direction]}</span> {a.direction}</span>}
                </p>
              ) : <p className="muted small">No structured grade</p>}
              {l.withheld ? <p className="scope-notice small">Withheld: pending human review.</p> : (
                <>
                  {s.excerpt && s.lines.length > 0 && <p className="muted small">Excerpt</p>}
                  <ul className="key-points">{s.lines.map((line) => <li key={line}>{line}</li>)}</ul>
                </>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
