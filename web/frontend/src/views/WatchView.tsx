import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { LiveRunSummary, Scope } from "../api/types";
import type { LiveRunControls } from "../state/useLiveRun";
import { LiveRunView } from "./CompareView";

// Watching runs the app didn't start (an agent session over MCP, or another browser). #/live
// lists the runs in progress; #/live/{id} follows one with the same live lanes a run started
// here gets. The events come from GET /api/runs/{id}/live, served at the viewer's scope.

const REFRESH_MS = 5000;

export function LiveList() {
  const [runs, setRuns] = useState<LiveRunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.liveRuns()
      .then((r) => { if (alive) { setRuns(r); setError(null); } })
      .catch((e: Error) => { if (alive) setError(e.message); });
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);
  return (
    <section className="page" aria-labelledby="live-list-title">
      <h1 id="live-list-title">Runs in progress</h1>
      <p className="muted">
        Every compare run that is running right now, whoever started it: this app, the API, or an agent session
        (Claude Code, Codex, Cursor) connected over MCP. Open one to watch it step by step.
      </p>
      {error && <p role="alert" className="error">{error}</p>}
      {runs !== null && runs.length === 0 && <p className="muted">Nothing is running right now.</p>}
      {runs !== null && runs.length > 0 && (
        <ul className="live-list">
          {runs.map((r) => (
            <li key={r.run_id}>
              <a href={`#/live/${r.run_id}`}>{r.ticker || r.subject || r.run_id.slice(0, 8)}</a>
              <span className="muted"> · started by {r.started_by} · {r.run_id.slice(0, 8)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function WatchView({ runId, live, scope }: { runId: string; live: LiveRunControls; scope: Scope }) {
  const { watch } = live;
  useEffect(() => {
    if (live.state.runId !== runId) watch(scope, runId);
    // Re-watch only when the id or the viewer's scope changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, scope]);
  return <LiveRunView state={live.state} onReset={() => { live.reset(); window.location.hash = "/live"; }} />;
}
