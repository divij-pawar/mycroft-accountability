import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { isCompareRun, type Claim, type Flag, type Gate, type Run, type Scope } from "../api/types";
import { formatFigure, shortId } from "../lib/format";
import { Markdown, RolePill, Section, StatusBadge } from "../components/primitives";
import {
  Attempts, ClaimsTable, ComparisonSummary, ContextWindow, DataSourcesTable, ThoughtLog, VerificationBanner,
} from "../components/RecordSections";
import { TraceLog } from "../components/TraceLog";
import { DecisionGate } from "../components/DecisionGate";
import { SourcesProvider, sourcesOf } from "../components/SourcePopover";
import { AssessmentView, finalAttempt } from "../components/Assessment";

// A stored run, answer first: what happened → the answer and whether it checks
// out → the agent's reasoning and inputs → how the run went → raw record. Shown
// inline in the workspace (no modal), so it can sit beside anything else on screen.

export function RunDetailView({ run, flags, scope, onFlagged, onDecided }: {
  run: Run;
  flags: Flag[];
  scope: Scope;
  onFlagged?: (flag: Flag) => void;
  onDecided?: (gate: Gate) => void;
}) {
  const compare = isCompareRun(run);
  const objects = run.reasoning_objects ?? [];
  const chatClaims = Array.isArray(run.claims) ? (run.claims as Claim[]) : undefined;
  const thoughtWithheld = !compare && objects.length > 0 && !objects.some((o) => "thought_log" in o);

  const compareClaims = compare && run.claims && !Array.isArray(run.claims) ? run.claims : undefined;

  return (
    <SourcesProvider value={sourcesOf(run)}>
    <article className="run-detail" aria-labelledby="run-title">
      <header className="run-head">
        <div>
          <p className="eyebrow">Run <code title={run.run_id}>{shortId(run.run_id)}</code> · {compare ? "Compare" : "Chat"}</p>
          <h1 id="run-title" className="run-title">
            <RolePill role="user" /> {run.subject || run.ticker || "(no subject)"}
          </h1>
        </div>
        <div className="run-badges">
          {compare && <DownloadReview runId={run.run_id} scope={scope} />}
          {run.halted ? <StatusBadge state="halted" /> : <StatusBadge state="ok">Complete</StatusBadge>}
          {run.gate?.status === "AWAITING_DECISION" && <StatusBadge state="awaiting_decision" />}
          {run.gate?.status === "DECIDED" && <StatusBadge state="decided" />}
          {run.scope && <span className="badge-neutral">{run.scope}</span>}
        </div>
      </header>

      {run.error && (
        <div className="alert" role="alert">
          <RolePill role="verifier" /> {run.error}
        </div>
      )}
      {run.tool_capability_warning && <div className="alert alert-warn">{run.tool_capability_warning}</div>}

      {compare && run.cross_agent_comparison && (
        <ComparisonSummary
          cmp={run.cross_agent_comparison} producers={run.producers} facts={run.facts}
          withheld={run.withheld_pending_review}
          claims={compareClaims}
          objects={objects}
          decidedGrade={run.gate?.decided_grade}
          gate={run.gate && (
            <DecisionGate key={run.run_id} runId={run.run_id} gate={run.gate} scope={scope} onDecided={onDecided}
                          rows={run.cross_agent_comparison.metric_comparisons ?? []} />
          )}
        />
      )}

      {!compare && run.conclusion && (
        <section className="card" aria-label="Conclusion">
          <div className="card-head"><RolePill role="agent" /><h2 className="headline">Conclusion</h2></div>
          <Markdown text={run.conclusion} />
          <AssessmentView ro={finalAttempt(objects, undefined)} />
          <VerificationBanner claims={chatClaims} />
        </section>
      )}

      {!compare && (
        <dl className="kv">
          <div><dt><RolePill role="verifier" /> Confidence</dt>
            <dd>{run.confidence_score ?? "—"} <span className="badge-neutral">{run.confidence_classification ?? "—"}</span></dd></div>
          {run.consistency && (
            <div><dt><RolePill role="verifier" /> Consistency</dt>
              <dd><span className="badge-neutral">{run.consistency.agreement}</span> {run.consistency.score}</dd></div>
          )}
          {typeof run.verification_rate === "number" && (
            <div><dt><RolePill role="verifier" /> Citations verified</dt>
              <dd>{formatFigure(run.verification_rate * 100, "%")}</dd></div>
          )}
          <div><dt><RolePill role="verifier" /> Directive</dt><dd>{run.session?.directive_version ?? "—"}</dd></div>
        </dl>
      )}

      <ThoughtLog text={run.thought_log} withheld={thoughtWithheld} />
      <ContextWindow objects={objects} />
      <TraceLog steps={run.steps} />
      <DataSourcesTable sources={run.data_sources} />
      <ClaimsTable claims={chatClaims} />
      <Attempts objects={objects} />
      <Flags runId={run.run_id} flags={flags} scope={scope} onFlagged={onFlagged} />
      <details className="section raw-record">
        <summary><span className="section-title">Technical details: the raw record</span></summary>
        <div className="section-body">
          {compare && (
            <button type="button" className="btn-ghost small" onClick={() => api.downloadAudit(run.run_id, scope)}>
              Download the audit record (JSON)
            </button>
          )}
          <pre className="prompt-text">{JSON.stringify(run, null, 2)}</pre>
        </div>
      </details>
    </article>
    </SourcesProvider>
  );
}

/** Markdown by default (AGENTS.md: Markdown for humans); JSON is under Technical details. */
function DownloadReview({ runId, scope }: { runId: string; scope: Scope }) {
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <button type="button" className="btn-ghost small" title="A readable review of this run, at your current scope"
              onClick={() => api.downloadReview(runId, scope).catch((e: Error) => setError(e.message))}>
        Download review
      </button>
      {error && <span className="error-text small" role="alert">{error}</span>}
    </>
  );
}

function Flags({ runId, flags, scope, onFlagged }: {
  runId: string; flags: Flag[]; scope: Scope; onFlagged?: (flag: Flag) => void;
}) {
  const [type, setType] = useState<Flag["flag_type"]>("Hallucinated");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      onFlagged?.(await api.addFlag(scope, runId, type, note));
      setNote("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Section title={`Reviewer flags (${flags.length})`} defaultOpen={flags.length > 0}>
      {flags.length ? (
        <ul className="plain-list">
          {flags.map((f) => (
            <li key={f.flag_id} className="flag-row">
              <span className="badge badge-warning">{f.flag_type}</span>
              <span className="muted small">{f.flagged_at}</span>
              {f.reviewer_note && <p>{f.reviewer_note}</p>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No flags.</p>
      )}
      {scope === "auditor" ? (
        <form className="flag-form" onSubmit={submit}>
          <label>
            Flag as
            <select value={type} onChange={(e) => setType(e.target.value as Flag["flag_type"])}>
              <option value="Hallucinated">Hallucinated</option>
              <option value="Incorrect">Incorrect</option>
              <option value="Other">Other</option>
            </select>
          </label>
          <label className="grow">
            Note
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What's wrong, in your words" />
          </label>
          <button type="submit" className="btn" disabled={saving}>{saving ? "Saving…" : "Add flag"}</button>
          {error && <p className="error-text" role="alert">{error}</p>}
        </form>
      ) : (
        <p className="muted small">Switch to auditor scope to add a flag.</p>
      )}
    </Section>
  );
}

/** Loads a run and its flags, then renders it. Reads at the viewer's scope, so an
 * investor gets the decision gate's withholding from the server itself. */
export function RunDetail({ runId, scope, onChanged }: { runId: string; scope: Scope; onChanged?: () => void }) {
  const [run, setRun] = useState<Run | null>(null);
  const [flags, setFlags] = useState<Flag[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const shown = useRef("");

  useEffect(() => {
    let live = true;
    // A re-read after a decision keeps the current view on screen (no flash, and
    // focus stays put); a different run or scope clears it first.
    const key = `${runId}|${scope}`;
    if (shown.current !== key) setRun(null);
    shown.current = key;
    setError(null);
    Promise.all([api.run(runId, scope), api.flags(runId).catch(() => [])])
      .then(([r, f]) => { if (live) { setRun(r); setFlags(f); } })
      .catch((err: Error) => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [runId, scope, version]);

  if (error) return <div className="alert" role="alert">Couldn't load this run: {error}</div>;
  if (!run) return <div className="skeleton" aria-busy="true">Loading run…</div>;
  return (
    <RunDetailView
      run={run} flags={flags} scope={scope} onFlagged={(f) => setFlags((fs) => [...fs, f])}
      // Re-read the run: the server decides what this scope may now see.
      onDecided={() => { setVersion((v) => v + 1); onChanged?.(); }}
    />
  );
}
