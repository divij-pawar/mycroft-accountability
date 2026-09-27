import { useEffect, useId, useState } from "react";
import type { Scope, Step } from "../api/types";
import type { CompareRequestBody } from "../api/stream";
import { Markdown, RolePill, StatusBadge } from "../components/primitives";
import { laneLine, laneSources, ranSequentially, stepsOf, type LiveState, type Slot } from "../state/liveRun";
import type { LiveRunControls } from "../state/useLiveRun";

// U4 — start a compare run and watch it happen. Two agent lanes (blue A, orange B)
// show what each agent is doing from real stream events; the one shared SEC fetch
// is drawn once, above both lanes, because it happened once. When the run is
// stored the app opens its record (App.tsx), so the answer is read from the store.

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}

// After this long a lost run is probably not coming back (a server restart ends it);
// saying "it will appear" forever would be a promise nothing backs.
const LOST_PATIENCE_MS = 10 * 60 * 1000;

const seconds = (since: string | undefined, now: number) =>
  since ? Math.max(0, Math.round((now - Date.parse(since)) / 1000)) : 0;

// ── Start form ─────────────────────────────────────────────────────────────────

const TICKER_RE = /^[A-Za-z][A-Za-z.-]{0,9}$/;

export function CompareForm({ onStart, disabled }: {
  onStart: (body: CompareRequestBody, title: string) => void;
  disabled: boolean;
}) {
  const id = useId();
  const [mode, setMode] = useState<"ticker" | "subject">("ticker");
  const [ticker, setTicker] = useState("");
  const [pairing, setPairing] = useState<"lenses" | "bull_bear">("lenses");
  const [subject, setSubject] = useState("");
  const [context, setContext] = useState("");
  const [modelA, setModelA] = useState("");
  const [modelB, setModelB] = useState("");

  const valid = mode === "ticker" ? TICKER_RE.test(ticker.trim()) : subject.trim().length >= 3;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid || disabled) return;
    const models = { ...(modelA.trim() && { agent_a_model: modelA.trim() }), ...(modelB.trim() && { agent_b_model: modelB.trim() }) };
    if (mode === "ticker") {
      onStart({ ticker: ticker.trim().toUpperCase(), ...(pairing === "bull_bear" && { pairing }), ...models },
              ticker.trim().toUpperCase());
    }
    else onStart({ subject: subject.trim(), context: context.trim(), ...models }, subject.trim());
  };

  return (
    <form className="card compare-form" onSubmit={submit} aria-labelledby={`${id}-h`}>
      <div className="card-head"><RolePill role="user" /><h1 id={`${id}-h`} className="headline">Compare two agents</h1></div>
      <fieldset className="mode-pick">
        <legend className="sr-only">What to compare</legend>
        {([["ticker", "A company (SEC filings)", "Each agent reads its own slice of the latest filing; both get net income and diluted EPS."],
           ["subject", "Any question", "Both agents get the same question and context."]] as const).map(([m, label, hint]) => (
          <label key={m} className={`radio-card ${mode === m ? "checked" : ""}`}>
            <input type="radio" name={`${id}-mode`} value={m} checked={mode === m} onChange={() => setMode(m)} />
            <span className="radio-title">{label}</span>
            <span className="muted small">{hint}</span>
          </label>
        ))}
      </fieldset>
      {mode === "ticker" ? (
        <>
          <label className="gate-field">
            Ticker
            <input value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="AAPL" autoCapitalize="characters"
                   spellCheck={false} maxLength={10} />
          </label>
          <fieldset className="mode-pick">
            <legend className="small muted">Which two agents</legend>
            {([["lenses", "Filings agent vs earnings agent", "Each reads its own part of the filing."],
               ["bull_bear", "Bull vs bear", "Both read the same figures; one argues for the company, one against."]] as const).map(([v, label, hint]) => (
              <label key={v} className={`radio-card ${pairing === v ? "checked" : ""}`}>
                <input type="radio" name={`${id}-pairing`} value={v} checked={pairing === v} onChange={() => setPairing(v)} />
                <span className="radio-title">{label}</span>
                <span className="muted small">{hint}</span>
              </label>
            ))}
          </fieldset>
        </>
      ) : (
        <>
          <label className="gate-field">
            Question
            <input value={subject} onChange={(e) => setSubject(e.target.value)}
                   placeholder="In what year was the first Pokémon game released in North America?" />
          </label>
          <label className="gate-field">
            Context (optional)
            <textarea rows={3} value={context} onChange={(e) => setContext(e.target.value)}
                      placeholder="Anything both agents should be given" />
          </label>
        </>
      )}
      <details className="tech">
        <summary>Models (optional)</summary>
        <div className="model-fields">
          <label className="gate-field">Agent A model<input value={modelA} onChange={(e) => setModelA(e.target.value)} placeholder="server default" /></label>
          <label className="gate-field">Agent B model<input value={modelB} onChange={(e) => setModelB(e.target.value)} placeholder="server default" /></label>
        </div>
      </details>
      <div className="gate-actions">
        <button type="submit" className="btn" disabled={!valid || disabled}>Run both agents</button>
        {disabled && <span className="muted small">One run at a time: wait for the current run to finish.</span>}
      </div>
    </form>
  );
}

// ── Live run ───────────────────────────────────────────────────────────────────

function SharedStep({ step, now }: { step: Step; now: number }) {
  const done = step.duration_ms !== null;
  const label = step.label === "fetch_company_facts" ? `SEC company facts${step.cik ? ` (CIK ${step.cik})` : ""}`
    : step.label === "lookup_cik" ? "SEC ticker list"
    : step.label.startsWith("summarize") ? `Prepared ${step.label.includes("earnings") ? "agent B's" : "agent A's"} inputs`
    : step.label;
  if (!done) {
    return (
      <li className="live-step running">
        <span className="spinner" aria-hidden="true" /> {step.kind === "fetch" ? "Fetching" : "Working on"} {label}… {seconds(step.started_at, now)}s
      </li>
    );
  }
  return (
    <li className="live-step">
      <StatusBadge state={step.status === "ok" ? "ok" : "error"}>{step.status === "ok" ? "Done" : "Failed"}</StatusBadge>
      {step.url
        ? <a className="cite-pill" href={step.url} target="_blank" rel="noopener noreferrer">{label}</a>
        : <span>{label}</span>}
      <span className="muted small">{(step.duration_ms! / 1000).toFixed(1)}s</span>
    </li>
  );
}

function Lane({ state, slot, now }: { state: LiveState; slot: Slot; now: number }) {
  const p = state.producers?.[slot];
  const line = laneLine(state, slot);
  const sources = laneSources(state, slot);
  const agent = state.agents[slot];
  const attempts = stepsOf(state, `agent_${slot}`).filter((s) => s.kind === "llm").length;
  return (
    <article className={`agent-lane lane-${slot}`} aria-label={`Agent ${slot.toUpperCase()}`}>
      <header className="lane-head">
        <span className="lane-letter" aria-hidden="true">{slot.toUpperCase()}</span>
        <RolePill role="agent" />
        <span>{p ? p.role.toLowerCase().replace("producer", "agent") : `agent ${slot}`}</span>
        {p && <span className="badge-neutral">{p.model}</span>}
      </header>
      <p className="lane-status" data-kind={line.kind}>
        {line.kind === "waiting" && <span className="muted">Waiting to start</span>}
        {line.kind === "thinking" && (
          <><span className="spinner" aria-hidden="true" /> {line.attempt > 1 ? `Retrying (attempt ${line.attempt})` : "Thinking"}… {seconds(line.since, now)}s</>
        )}
        {line.kind === "searching" && (
          <><span className="spinner" aria-hidden="true" /> Searching{line.query ? <>: <q>{line.query}</q></> : ""}… {seconds(line.since, now)}s</>
        )}
        {line.kind === "extracting" && (
          <><span className="spinner" aria-hidden="true" /> Reading its answer for a grade… {seconds(line.since, now)}s</>
        )}
        {line.kind === "between" && <span className="muted">{line.label}</span>}
        {line.kind === "finished" && (
          line.halted ? <StatusBadge state="halted">Halted: failed the format check twice</StatusBadge>
                      : <StatusBadge state="ok">Finished{attempts > 1 ? ` after ${attempts} attempts` : ""}</StatusBadge>
        )}
      </p>
      {sources.length > 0 && (
        <div className="cite-pills" aria-label={`Sources agent ${slot.toUpperCase()} found`}>
          {sources.slice(0, 6).map((s) => (
            <a key={s.url} className="cite-pill" href={s.url} target="_blank" rel="noopener noreferrer"
               title={s.query ? `Found by searching "${s.query}"` : undefined}>
              {s.title || new URL(s.url).hostname}
            </a>
          ))}
          {sources.length > 6 && <span className="muted small">+{sources.length - 6} more</span>}
        </div>
      )}
      {agent.finished && agent.conclusion && <Markdown text={agent.conclusion} />}
    </article>
  );
}

export function LiveRunView({ state, onReset }: { state: LiveState; onReset: () => void }) {
  const active = state.status === "starting" || state.status === "running" || state.status === "lost";
  const now = useNow(active);
  const shared = stepsOf(state, "shared");
  const bothDone = state.agents.a.finished && state.agents.b.finished;

  return (
    <section className="live-run" aria-labelledby="live-title">
      <header className="run-head">
        <div>
          <p className="eyebrow">Live run{state.runId ? ` · ${state.runId.slice(0, 8)}` : ""}</p>
          <h1 id="live-title" className="run-title"><RolePill role="user" /> {state.title}</h1>
        </div>
        <div className="run-badges">
          {active && state.status !== "lost" && <StatusBadge state="running">Running</StatusBadge>}
          {state.status === "done" && <StatusBadge state="ok">Done</StatusBadge>}
          {state.status === "failed" && <StatusBadge state="error">Failed</StatusBadge>}
        </div>
      </header>

      {state.status === "lost" && (
        <div className="alert alert-warn" role="status">
          {state.lostAt !== null && now - state.lostAt > LOST_PATIENCE_MS
            ? <>Still not stored {Math.round((now - state.lostAt) / 60000)} minutes after the connection dropped. If the
                server restarted, this run was lost with it; if the model is stuck, it may never finish. Still checking
                every 5 s. <button type="button" className="btn-ghost" onClick={onReset}>Start another run</button></>
            : "Connection lost. The run continues on the server; it will open here as soon as it's stored (checking every 5 s)."}
        </div>
      )}
      {state.status === "failed" && (
        <div className="alert" role="alert">
          {state.error}
          <button type="button" className="btn-ghost" onClick={onReset}>Start another run</button>
        </div>
      )}
      {state.status === "done" && state.result && !state.result.session && (
        <div className="alert" role="alert">
          {state.result.error || "The run ended without a stored record."}
          <button type="button" className="btn-ghost" onClick={onReset}>Start another run</button>
        </div>
      )}

      {shared.length > 0 && (
        <div className="card shared-work">
          <div className="card-head"><RolePill role="verifier" /><h2 className="subhead">Shared: one fetch, both agents</h2></div>
          <ul className="plain-list">{shared.map((s) => <SharedStep key={s.seq} step={s} now={now} />)}</ul>
        </div>
      )}

      <div className="agents-grid">
        <Lane state={state} slot="a" now={now} />
        <Lane state={state} slot="b" now={now} />
      </div>
      {ranSequentially(state) && (
        <p className="muted small" role="note">
          Agent B started only after agent A finished: the agents ran one at a time here, not side by side
          (the local model server handles one request at a time, or concurrency is set to 1).
        </p>
      )}
      {bothDone && state.status === "running" && (
        <p className="live-step running"><span className="spinner" aria-hidden="true" /> Comparing the two answers…</p>
      )}
    </section>
  );
}

export function CompareView({ live, scope }: { live: LiveRunControls; scope: Scope }) {
  const { state } = live;
  if (state.status === "idle") {
    return <CompareForm disabled={false} onStart={(body, title) => live.start(scope, body, title)} />;
  }
  return <LiveRunView state={state} onReset={live.reset} />;
}
