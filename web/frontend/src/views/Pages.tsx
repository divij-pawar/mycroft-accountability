import { useEffect, useId, useState } from "react";
import { api, type Config } from "../api/client";
import type { KnownIssue, Scope, SelfReport } from "../api/types";
import { Markdown, RolePill, StatusBadge } from "../components/primitives";
import { phaseLine, stepsOf } from "../state/liveRun";
import type { LiveRunControls } from "../state/useLiveRun";

// U9 — the pages that lived only in the classic UI, so "/" can switch to this app
// (the plan's parity checklist): chat, settings, the directive, the Honest Ledger.
// Each is a page in the workspace, not a modal over it.

function useLoad<T>(load: () => Promise<T>): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    load().then((d) => { if (live) setData(d); }).catch((e: Error) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error };
}

// ── Chat ───────────────────────────────────────────────────────────────────────

export function ChatView({ live, scope }: { live: LiveRunControls; scope: Scope }) {
  const id = useId();
  const [message, setMessage] = useState("");
  const [context, setContext] = useState("");
  const [showContext, setShowContext] = useState(false);
  const { state } = live;
  const [now, setNow] = useState(() => Date.now());
  const active = state.status === "starting" || state.status === "running";
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);

  if (state.status !== "idle") {
    const line = phaseLine(state, "chat");
    const secs = (since?: string) => (since ? Math.max(0, Math.round((now - Date.parse(since)) / 1000)) : 0);
    const searches = stepsOf(state, "chat").filter((s) => s.kind === "tool" && s.tool_phase === "finished");
    return (
      <section className="live-run" aria-labelledby={`${id}-t`}>
        <header className="run-head">
          <div>
            <p className="eyebrow">Live chat run{state.runId ? ` · ${state.runId.slice(0, 8)}` : ""}</p>
            <h1 id={`${id}-t`} className="run-title"><RolePill role="user" /> {state.title}</h1>
          </div>
          {active && <StatusBadge state="running">Running</StatusBadge>}
        </header>
        <article className="agent-lane lane-a" aria-label="Agent">
          <p className="lane-status">
            {line.kind === "waiting" && <span className="muted">Waiting to start</span>}
            {line.kind === "thinking" && <><span className="spinner" aria-hidden="true" /> {line.attempt > 1 ? `Retrying (attempt ${line.attempt})` : "Thinking"}… {secs(line.since)}s</>}
            {line.kind === "searching" && <><span className="spinner" aria-hidden="true" /> Searching{line.query ? <>: <q>{line.query}</q></> : ""}… {secs(line.since)}s</>}
            {line.kind === "extracting" && <><span className="spinner" aria-hidden="true" /> Reading its answer… {secs(line.since)}s</>}
            {line.kind === "between" && <span className="muted">{line.label}</span>}
          </p>
          {searches.length > 0 && (
            <div className="cite-pills" aria-label="Sources the agent found">
              {searches.flatMap((s) => s.urls ?? []).slice(0, 6).map((u) => (
                <a key={u} className="cite-pill" href={u} target="_blank" rel="noopener noreferrer">{new URL(u).hostname}</a>
              ))}
            </div>
          )}
        </article>
        {state.status === "lost" && (
          <div className="alert alert-warn" role="status">Connection lost. The run continues on the server; it will open here once it's stored.</div>
        )}
        {(state.status === "failed" || (state.status === "done" && !state.result?.session)) && (
          <div className="alert" role="alert">
            {state.error ?? state.result?.error ?? "The run ended without a stored record."}
            <button type="button" className="btn-ghost" onClick={live.reset}>Ask again</button>
          </div>
        )}
      </section>
    );
  }

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (message.trim().length < 2) return;
    live.startChat(scope, { message: message.trim(), context: context.trim() });
  };
  return (
    <form className="card compare-form" onSubmit={submit} aria-labelledby={`${id}-h`}>
      <div className="card-head"><RolePill role="user" /><h1 id={`${id}-h`} className="headline">Ask one agent</h1></div>
      <p className="muted small">One agent answers under the directive; its sources are checked, and the run is stored.</p>
      <label className="gate-field">
        Question
        <textarea rows={3} value={message} onChange={(e) => setMessage(e.target.value)} placeholder="What was Apple's revenue last quarter?" />
      </label>
      <button type="button" className="btn-ghost small" aria-expanded={showContext} onClick={() => setShowContext((s) => !s)}>
        {showContext ? "Hide context" : "Add context"}
      </button>
      {showContext && (
        <label className="gate-field">
          Context (optional)
          <textarea rows={4} value={context} onChange={(e) => setContext(e.target.value)} placeholder="Anything the agent should be given" />
        </label>
      )}
      <div className="gate-actions">
        <button type="submit" className="btn" disabled={message.trim().length < 2 || live.running}>Ask</button>
        {live.running && <span className="muted small">One run at a time: wait for the current run to finish.</span>}
      </div>
    </form>
  );
}

// ── Settings ───────────────────────────────────────────────────────────────────

const AGENT_IDS = ["external", "financial", "earnings", "patent", "competitive"];

export function SettingsView({ scope }: { scope: Scope }) {
  const { data, error } = useLoad(api.config);
  const [cfg, setCfg] = useState<Config | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  useEffect(() => { if (data) setCfg(data); }, [data]);

  if (error) return <div className="alert" role="alert">Couldn't load settings: {error}</div>;
  if (!cfg) return <div className="skeleton" aria-busy="true">Loading settings…</div>;
  const readOnly = scope !== "auditor";
  const save = async (patch: Partial<Config>) => {
    setSaveError(null);
    try {
      setCfg(await api.setConfig(patch));
      setSaved(`Saved ${Object.keys(patch).join(", ")}`);
    } catch (e) { setSaveError((e as Error).message); }
  };
  return (
    <section className="card compare-form" aria-labelledby="settings-h">
      <div className="card-head"><RolePill role="user" /><h1 id="settings-h" className="headline">Run settings</h1></div>
      <p className="muted small">
        Apply to every new run on this server and are recorded with each run. {readOnly && "Switch to auditor scope to change them."}
      </p>
      <dl className="kv">
        <div><dt>Model</dt><dd><code>{cfg.model}</code> <span className="muted small">(per-agent models are set when starting a compare)</span></dd></div>
      </dl>
      <label className="gate-field">
        Temperature: {cfg.temperature}
        <input type="range" min={0} max={1} step={0.05} value={cfg.temperature} disabled={readOnly}
               onChange={(e) => setCfg({ ...cfg, temperature: Number(e.target.value) })}
               onMouseUp={() => save({ temperature: cfg.temperature })} onKeyUp={() => save({ temperature: cfg.temperature })} />
      </label>
      <label className="gate-field">
        Seed
        <input type="number" value={cfg.seed} disabled={readOnly} onChange={(e) => setCfg({ ...cfg, seed: Number(e.target.value) })}
               onBlur={() => save({ seed: cfg.seed })} />
      </label>
      <label className="gate-field">
        Chat agent identity
        <select value={cfg.agent_id} disabled={readOnly} onChange={(e) => save({ agent_id: e.target.value })}>
          {AGENT_IDS.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
      </label>
      <label className="gate-field">
        Starting confidence score: {cfg.confidence_score}
        <input type="range" min={0} max={1} step={0.05} value={cfg.confidence_score} disabled={readOnly}
               onChange={(e) => setCfg({ ...cfg, confidence_score: Number(e.target.value) })}
               onMouseUp={() => save({ confidence_score: cfg.confidence_score })} onKeyUp={() => save({ confidence_score: cfg.confidence_score })} />
        <span className="muted small">Below 0.4 a run is classed high-uncertainty (ADR-08).</span>
      </label>
      <label className="gate-field row">
        <input type="checkbox" checked={cfg.consistency_probe} disabled={readOnly}
               onChange={(e) => save({ consistency_probe: e.target.checked })} />
        Consistency probe on chat runs (asks the agent twice and compares)
      </label>
      <p className="small" role="status">{saveError ? <span className="error-text">{saveError}</span> : saved}</p>
    </section>
  );
}

// ── Directive ──────────────────────────────────────────────────────────────────

export function DirectiveView() {
  const { data, error } = useLoad(api.directive);
  if (error) return <div className="alert" role="alert">Couldn't load the directive: {error}</div>;
  if (!data) return <div className="skeleton" aria-busy="true">Loading the directive…</div>;
  return (
    <section className="card" aria-labelledby="dir-h">
      <div className="card-head">
        <RolePill role="verifier" />
        <h1 id="dir-h" className="headline">Active directive</h1>
        <span className="badge-neutral">{data.version}</span>
      </div>
      <p className="muted small">The system prompt every agent runs under. Versioned; each run records the version it used.</p>
      <pre className="prompt-text directive-text">{data.text}</pre>
    </section>
  );
}

// ── Honest Ledger ──────────────────────────────────────────────────────────────

const STATUS_STATE: Record<KnownIssue["status"], Parameters<typeof StatusBadge>[0]["state"]> = {
  OPEN: "mismatch", UNVERIFIED: "unverifiable_period", RESOLVED: "match", BY_DESIGN: "unchecked",
};
const STATUS_WORD: Record<KnownIssue["status"], string> = {
  OPEN: "Open", UNVERIFIED: "Not yet shown to work", RESOLVED: "Fixed", BY_DESIGN: "By design",
};

export function LedgerView({ onCount }: { onCount?: (open: number) => void }) {
  const { data, error } = useLoad<SelfReport>(api.selfReport);
  const [show, setShow] = useState<"open" | "all">("open");
  useEffect(() => { if (data) onCount?.(data.counts.issues_open); }, [data, onCount]);
  if (error) return <div className="alert" role="alert">Couldn't load the Honest Ledger: {error}</div>;
  if (!data) return <div className="skeleton" aria-busy="true">Loading the Honest Ledger…</div>;
  const rank = { critical: 0, high: 1, medium: 2, low: 3, info: 4 } as const;
  const issues = [...data.known_issues]
    .filter((i) => show === "all" || i.status === "OPEN" || i.status === "UNVERIFIED")
    .sort((x, y) => rank[x.severity] - rank[y.severity]);
  return (
    <section className="card" aria-labelledby="ledger-h">
      <div className="card-head">
        <RolePill role="verifier" />
        <h1 id="ledger-h" className="headline">Honest Ledger</h1>
      </div>
      <p className="small">
        What has actually been tested, and what is actually broken. <strong>{data.counts.issues_open} open</strong> of{" "}
        {data.counts.issues_total} recorded · {data.counts.issues_critical} critical ·{" "}
        {data.automated_tests.total ?? "?"} automated tests · deployment: {data.deployment_status.state}.
      </p>
      <p className="muted small">{data.deployment_status.detail}</p>
      <div className="trace-toolbar" role="group" aria-label="Show">
        {(["open", "all"] as const).map((v) => (
          <button key={v} type="button" className={`chip ${show === v ? "chip-active" : ""}`} aria-pressed={show === v} onClick={() => setShow(v)}>
            {v === "open" ? "Still open" : "Everything"}
          </button>
        ))}
      </div>
      <ul className="plain-list ledger">
        {issues.map((i) => (
          <li key={i.id} className="ledger-row">
            <details>
              <summary>
                <StatusBadge state={STATUS_STATE[i.status]}>{STATUS_WORD[i.status]}</StatusBadge>
                <span className="badge-neutral">{i.severity}</span>
                <span className="badge-neutral">{i.area}</span>
                <strong>{i.title}</strong>
              </summary>
              <div className="ledger-detail small"><Markdown text={i.detail} /><p className="muted">Source: {i.source}</p></div>
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}
