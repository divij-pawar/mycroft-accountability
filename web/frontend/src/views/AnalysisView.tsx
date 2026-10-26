import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api } from "../api/client";
import { streamAnalysis, type StreamEvent } from "../api/stream";
import type { AnalysisRecord, AnalysisStep, AnalysisSummary, EvidenceRef, MeasurementsResponse, Scope } from "../api/types";
import {
  FIGURE_MARK, SIGNAL_LETTERS, SIGNAL_WORDS, STEP_MARK, evidenceLabel, figureText, groundedLabel, groundedTone, headline, measurementLine, stepLine,
} from "../lib/analysis";

// Ask for a signal (financial_agent/PLAN.md phase 2). One request is one run over one frozen
// bundle of data. The page shows the steps as they happen, then the stored record as a card.
// Nothing here says the signal is right. Each cited figure is checked against the tool result it
// cites (validation/analysis_checks.py, no model): that says the figure was quoted correctly, not that
// the signal follows from it. The card still says "Not verified" until a second agent has checked it.

type Phase = "idle" | "running" | "lost" | "failed";

export function AnalysisView({ scope }: { scope: Scope }) {
  const id = useId();
  const [message, setMessage] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [steps, setSteps] = useState<AnalysisStep[]>([]);
  const [asking, setAsking] = useState<string | null>(null);
  const [record, setRecord] = useState<AnalysisRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<AnalysisSummary[]>([]);
  const [measured, setMeasured] = useState<MeasurementsResponse | null>(null);
  const abort = useRef<AbortController | null>(null);

  const loadHistory = useCallback(() => {
    api.analyses(scope).then(setHistory).catch(() => setHistory([]));
  }, [scope]);
  useEffect(loadHistory, [loadHistory]);
  useEffect(() => {
    let live = true;
    api.analysisMeasurements(scope).then((m) => { if (live) setMeasured(m); }).catch(() => { if (live) setMeasured(null); });
    return () => { live = false; };
  }, [scope]);
  useEffect(() => () => abort.current?.abort(), []);

  const onEvent = (e: StreamEvent) => {
    const data = e.data as Record<string, unknown>;
    if (e.event === "run_started") setAsking(String(data.ticker ?? ""));
    else if (e.event === "step") setSteps((s) => [...s, data as unknown as AnalysisStep]);
    else if (e.event === "result") { setRecord(data as unknown as AnalysisRecord); setPhase("idle"); loadHistory(); }
    else if (e.event === "error") { setError(String(data.error ?? "The run failed.")); setPhase("failed"); }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (message.trim().length < 2 || phase === "running") return;
    setPhase("running"); setSteps([]); setRecord(null); setError(null); setAsking(null);
    abort.current = new AbortController();
    try {
      const outcome = await streamAnalysis(scope, { message: message.trim() }, onEvent, abort.current.signal);
      setPhase((p) => (p === "running" ? (outcome === "lost" ? "lost" : "idle") : p));
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      setError((err as Error).message);
      setPhase("failed");
    }
  };

  const open = (runId: string) => {
    setError(null); setSteps([]);
    api.analysis(runId, scope).then(setRecord).catch((err: Error) => setError(`Couldn't open that analysis: ${err.message}`));
  };

  const running = phase === "running";
  return (
    <div className="analysis-layout">
      <section aria-labelledby={`${id}-h`}>
        <form className="card compare-form" onSubmit={submit}>
          <div className="card-head"><h1 id={`${id}-h`} className="headline">Ask for a signal</h1></div>
          <p className="muted small">
            Name a company and, if you like, how long you'd hold it. A model reads frozen price and filing data
            through recorded tools and gives a signal with its reasons. It is a research signal from a model, not
            investment advice, and it has not been checked.
          </p>
          {measured && <p className="model-line muted small" data-testid="model-line">{measurementLine(measured)}</p>}
          <label className="gate-field">
            Your question
            <textarea rows={3} value={message} onChange={(e) => setMessage(e.target.value)} disabled={running}
                      placeholder="Should I buy AAPL over the next 6 months?" />
          </label>
          <div className="gate-actions">
            <button type="submit" className="btn" disabled={running || message.trim().length < 2}>
              {running ? "Working…" : "Get a signal"}
            </button>
          </div>
        </form>

        {(running || steps.length > 0) && !record && (
          <section className="card analysis-progress" aria-label="Progress" role="status" aria-live="polite">
            <p className="eyebrow">{running ? <><span className="spinner" aria-hidden="true" /> Working on {asking ?? "your request"}</> : "What happened"}</p>
            <ol className="step-list">
              {steps.map((s) => (
                <li key={s.seq} data-status={s.status}>
                  <span aria-hidden="true">{STEP_MARK[s.status].mark}</span>
                  <span className="sr-only">{STEP_MARK[s.status].word}: </span>
                  {stepLine(s)}
                </li>
              ))}
            </ol>
          </section>
        )}

        {phase === "lost" && (
          <div className="alert alert-warn" role="status">
            Connection lost. The run continues on the server, and its record will appear in the list when it is stored.
          </div>
        )}
        {error && <div className="alert" role="alert">{error}</div>}
        {record && <AnalysisCard record={record} scope={scope} />}
      </section>

      <aside className="card analysis-history" aria-label="Earlier signals">
        <h2 className="headline">Earlier signals</h2>
        {history.length === 0 && <p className="muted small">None yet.</p>}
        <ul className="history-list">
          {history.map((h) => (
            <li key={h.run_id}>
              <button type="button" className="btn-ghost" aria-current={record?.run_id === h.run_id ? "true" : undefined}
                      onClick={() => open(h.run_id)}>
                <strong>{h.request.ticker}</strong>{" "}
                {h.output ? `${SIGNAL_LETTERS[h.output.signal]} · ${SIGNAL_WORDS[h.output.signal]}` : "no signal"}
                <span className="muted small"> · {h.created_at.slice(0, 16).replace("T", " ")}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
    </div>
  );
}

/** `grounded 1/3` with its tone as words too, and what the count means. */
function GroundingSummary({ tier1 }: { tier1: NonNullable<AnalysisRecord["tier1"]> }) {
  const tone = groundedTone(tier1);
  const word = tone === "danger" ? "contradicted" : tone === "warning" ? "partly unfound" : tone === "success" ? "all match" : "nothing to check";
  return (
    <p className="grounding" data-tone={tone}>
      <span className={`badge badge-${tone}`} data-testid="grounded-badge">{groundedLabel(tier1)}</span>{" "}
      <span className="muted small">
        {word}. Each figure it cites is checked against the data tool result it points at, with no model. That says the
        figures were quoted correctly, not that the signal follows from them.
        {tier1.user_asserted > 0 && ` ${tier1.user_asserted} from your document, not checked.`}
        {tier1.by_value_only > 0 && ` ${tier1.by_value_only} matched by value only.`}
      </span>
    </p>
  );
}

function problemFor(record: AnalysisRecord, keyPoint: number, ref: EvidenceRef): string | null {
  if (!("call" in ref)) return null;
  const p = record.tier1?.evidence_problems.find((e) => e.key_point === keyPoint && "call" in e.ref && e.ref.call === ref.call);
  return p ? p.reason : null;
}

/** One line per cited figure: a mark and its words, where it came from, and why when it failed. */
function FigureChecks({ tier1, record }: { tier1: NonNullable<AnalysisRecord["tier1"]>; record: AnalysisRecord }) {
  return (
    <>
      <h3 className="small-head">Figures it cited</h3>
      <ul className="figure-checks">
        {tier1.checks.map((c, i) => (
          <li key={i} data-status={c.status}>
            <span className="figure-mark" aria-hidden="true">{FIGURE_MARK[c.status].mark}</span>
            <span className="sr-only">{FIGURE_MARK[c.status].word}: </span>
            <span className="figure-text">{figureText(c)}</span>
            <span className="evidence"><span className="evidence-chip">{evidenceLabel(c.ref, record.tool_calls)}</span></span>
            {c.status !== "GROUNDED" && <span className="muted small figure-reason"> {c.reason}</span>}
            {c.status === "GROUNDED" && c.matched_by === "value" && <span className="muted small figure-reason"> matched by value only</span>}
          </li>
        ))}
      </ul>
    </>
  );
}

export function AnalysisCard({ record, scope }: { record: AnalysisRecord; scope: Scope }) {
  const id = useId();
  const out = record.output;
  return (
    <article className="card signal-card" aria-labelledby={`${id}-t`} data-status={record.status}>
      <p className="eyebrow">{record.request.ticker} · data as of {record.data_as_of}</p>
      {out ? (
        <>
          <h2 id={`${id}-t`} className="signal-headline">{headline(record)}</h2>
          {record.conclusion && <p>{record.conclusion}</p>}
          {record.tier1 && <GroundingSummary tier1={record.tier1} />}
          {out.key_points.length > 0 && (
            <>
              <h3 className="small-head">Why</h3>
              <ul className="reasons">
                {out.key_points.map((p, i) => (
                  <li key={i}>
                    {p.text}
                    <span className="evidence">
                      {p.evidence.map((ref, j) => (
                        <span key={j} className="evidence-chip" data-problem={problemFor(record, i + 1, ref) ? "true" : undefined}>
                          {evidenceLabel(ref, record.tool_calls)}
                          {problemFor(record, i + 1, ref) && <> · <span className="chip-flag">{problemFor(record, i + 1, ref)}</span></>}
                        </span>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
              {!record.tier1 && (
                <p className="muted small">
                  A reference says where the model says a figure came from. It has not been checked against the data.
                </p>
              )}
            </>
          )}
          {record.tier1 && record.tier1.checks.length > 0 && <FigureChecks tier1={record.tier1} record={record} />}
          {record.issues.length > 0 && (
            <div className="alert alert-warn" role="status">
              <strong>Adjusted:</strong>
              <ul>{record.issues.map((m, i) => <li key={i}>{m}</li>)}</ul>
            </div>
          )}
        </>
      ) : (
        <>
          <h2 id={`${id}-t`} className="signal-headline">No signal</h2>
          <p>
            The model's reply broke the required format {record.attempts.length > 1 ? "twice" : "once"}, so nothing was
            recorded as a signal. {record.halt_reason ? <span className="muted">({record.halt_reason})</span> : null}
          </p>
        </>
      )}

      <details>
        <summary>{record.agent.reasoning === "native" ? "Thinking" : "Working notes"}</summary>
        {record.thought_log
          ? <pre className="notes">{record.thought_log}</pre>
          : <p className="muted small">{scope === "investor" ? "Withheld at investor scope." : "None recorded."}</p>}
      </details>
      <details>
        <summary>Attempts ({record.attempts.length})</summary>
        <ol className="step-list">
          {record.attempts.map((a) => (
            <li key={a.attempt}>
              Attempt {a.attempt}: {a.parse_status === "SUCCESS" ? "passed the format check"
                : a.parse_status === "HALT" ? "broke the format again, so the run stopped" : "broke the format"}
              {a.raw_text ? <pre className="notes">{a.raw_text}</pre>
                : a.withheld ? <span className="muted small"> (reply withheld at investor scope)</span> : null}
            </li>
          ))}
        </ol>
      </details>

      <footer className="signal-foot">
        <span className="badge badge-neutral">Not verified</span>
        <span className="muted small">
          {record.agent.model} · {record.agent.reasoning === "native" ? "thinks natively" : "no native reasoning"} ·
          prompt {record.agent.prompt_version} · {record.tool_calls.length} data {record.tool_calls.length === 1 ? "call" : "calls"}
        </span>
        <span className="muted small">A research signal from a model, not investment advice.</span>
      </footer>
    </article>
  );
}
