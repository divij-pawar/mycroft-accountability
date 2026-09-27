import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api } from "../api/client";
import { isCompareRun, type Comparison, type Run, type Scope, type Session } from "../api/types";
import { shortId } from "../lib/format";
import { StatusBadge } from "../components/primitives";
import { matrixCounts } from "../components/ComparisonMatrix";

// Runs / Sessions / Flagged — the legacy right-hand panel, as a proper tablist
// (arrow keys move between tabs, per the WAI-ARIA tabs pattern).

export type Tab = "runs" | "sessions" | "flagged" | "decide";
const TABS: { id: Tab; label: string }[] = [
  { id: "runs", label: "Runs" },
  { id: "sessions", label: "Sessions" },
  { id: "flagged", label: "Flagged" },
  { id: "decide", label: "Decide" }, // short: four tabs share a ~300px panel
];

export const needsDecision = (r: Run) => r.gate?.status === "AWAITING_DECISION";

/**
 * The same reading the run's own headline gives (RecordSections summarize()): "differ"
 * only for real mismatches; a run flagged for one-sided figures "needs review".
 */
function Outcome({ cmp }: { cmp: Comparison }) {
  const rows = cmp.metric_comparisons;
  if (!rows) {
    return cmp.contradiction_flag
      ? <StatusBadge state="mismatch">Figures differ ({cmp.divergent_numbers.length})</StatusBadge>
      : <StatusBadge state="match">Figures agree</StatusBadge>;
  }
  const c = matrixCounts(rows);
  if (c.mismatched) return <StatusBadge state="mismatch">Mismatch · {c.mismatched} of {c.shared} figures</StatusBadge>;
  if (cmp.contradiction_flag || c.flagged) {
    const n = c.uncorroborated + c.derivedWrong;
    return <StatusBadge state="uncorroborated">Needs review{n ? ` (${n})` : ""}</StatusBadge>;
  }
  return <StatusBadge state="match">Figures agree</StatusBadge>;
}

function RunCard({ run, selected, onSelect }: { run: Run; selected: boolean; onSelect: (id: string) => void }) {
  const compare = isCompareRun(run);
  const cmp = run.cross_agent_comparison;
  return (
    <li>
      <button
        type="button"
        className={`run-card ${selected ? "selected" : ""}`}
        aria-current={selected ? "true" : undefined}
        onClick={() => onSelect(run.run_id)}
      >
        <span className="run-card-top">
          {run.halted ? <StatusBadge state="halted" /> : <StatusBadge state="ok">Done</StatusBadge>}
          <span className="badge-neutral">{compare ? "Compare" : "Chat"}</span>
          <code className="muted small">{shortId(run.run_id)}</code>
        </span>
        <span className="run-card-subject">{run.subject || run.ticker || "(no subject)"}</span>
        {compare && cmp?.status === "COMPARED" && (
          <span className="small run-card-outcome">
            <Outcome cmp={cmp} />
            {run.gate?.decided_grade ? <StatusBadge state="decided">Grade {run.gate.decided_grade.grade}</StatusBadge>
              : cmp.synthesis?.consensus_grade ? <StatusBadge state="match">Consensus {cmp.synthesis.consensus_grade.grade}</StatusBadge>
              : null}
            {needsDecision(run) && <StatusBadge state="awaiting_decision" />}
            {run.gate?.status === "DECIDED" && !run.gate.decided_grade && <StatusBadge state="decided" />}
          </span>
        )}
      </button>
    </li>
  );
}

export function HistoryPanel({ selectedId, onSelect, refreshKey = 0, scope, requestedTab, onNeedsDecision }: {
  selectedId: string | null;
  onSelect: (id: string) => void;
  refreshKey?: number;
  /** Read at this scope (investors get the gate's withholding); unset = stored scope. */
  scope?: Scope;
  /** Switch to this tab when it changes (the top bar's "needs a decision" chip). */
  requestedTab?: { tab: Tab; at: number };
  onNeedsDecision?: (count: number) => void;
}) {
  const [tab, setTab] = useState<Tab>("runs");
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [flagged, setFlagged] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tabRefs = useRef<Record<Tab, HTMLButtonElement | null>>({ runs: null, sessions: null, flagged: null, decide: null });

  const load = useCallback(() => {
    setError(null);
    Promise.all([api.runs(scope), api.sessions(scope), api.contradictions(scope)])
      .then(([r, s, f]) => { setRuns(r); setSessions(s); setFlagged(f); })
      .catch((err: Error) => setError(err.message));
  }, [scope]);

  useEffect(load, [load, refreshKey]);
  useEffect(() => { if (requestedTab) setTab(requestedTab.tab); }, [requestedTab]);

  const awaiting = runs ? runs.filter(needsDecision) : null;
  const awaitingCount = awaiting?.length;
  useEffect(() => {
    if (awaitingCount !== undefined) onNeedsDecision?.(awaitingCount);
  }, [awaitingCount, onNeedsDecision]);

  const onTabKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const delta = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!delta) return;
    e.preventDefault();
    const next = TABS[(i + delta + TABS.length) % TABS.length].id;
    setTab(next);
    tabRefs.current[next]?.focus();
  };

  const counts: Record<Tab, number | undefined> = {
    runs: runs?.length, sessions: sessions?.length, flagged: flagged?.length, decide: awaitingCount,
  };

  return (
    <section className="history" aria-label="History">
      <div className="history-head">
        <div role="tablist" aria-label="History" className="tabs">
          {TABS.map((t, i) => (
            <button
              key={t.id}
              ref={(el) => { tabRefs.current[t.id] = el; }}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`panel-${t.id}`}
              tabIndex={tab === t.id ? 0 : -1}
              className={`tab ${tab === t.id ? "tab-active" : ""}`}
              onClick={() => setTab(t.id)}
              onKeyDown={(e) => onTabKey(e, i)}
            >
              {t.label} <span className="count">{counts[t.id] ?? "…"}</span>
            </button>
          ))}
        </div>
        <button type="button" className="chip" onClick={load} aria-label="Refresh history">↻</button>
      </div>

      {error && <p className="error-text" role="alert">Couldn't load history: {error}</p>}

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="history-body">
        {tab === "runs" && <RunList runs={runs} selectedId={selectedId} onSelect={onSelect} empty="No runs yet." />}
        {tab === "flagged" && (
          <RunList runs={flagged} selectedId={selectedId} onSelect={onSelect}
                   empty="No compare runs with differing figures." />
        )}
        {tab === "decide" && (
          <RunList runs={awaiting} selectedId={selectedId} onSelect={onSelect}
                   empty="Nothing is waiting for a decision. A run lands here when the agents give different values for the same figure." />
        )}
        {tab === "sessions" && (
          sessions === null ? <p className="muted">Loading…</p>
          : !sessions.length ? <p className="muted">No sessions yet.</p>
          : (
            <ul className="plain-list">
              {sessions.map((s) => (
                <li key={s.run_id}>
                  <button type="button" className="run-card" onClick={() => onSelect(s.run_id)}>
                    <span className="run-card-top">
                      <span className="badge-neutral">{s.status}</span>
                      <code className="muted small">{shortId(s.run_id)}</code>
                    </span>
                    <span className="run-card-subject">{s.ticker} · directive {s.directive_version}</span>
                  </button>
                </li>
              ))}
            </ul>
          )
        )}
      </div>
    </section>
  );
}

function RunList({ runs, selectedId, onSelect, empty }: {
  runs: Run[] | null; selectedId: string | null; onSelect: (id: string) => void; empty: string;
}) {
  if (runs === null) return <p className="muted">Loading…</p>;
  if (!runs.length) return <p className="muted">{empty}</p>;
  return (
    <ul className="plain-list">
      {runs.map((r) => <RunCard key={r.run_id} run={r} selected={r.run_id === selectedId} onSelect={onSelect} />)}
    </ul>
  );
}
