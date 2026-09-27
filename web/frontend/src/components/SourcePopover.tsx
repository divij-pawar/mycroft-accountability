import { createContext, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { Check, Excerpt, Fact, Occurrence, Run, Snippet } from "../api/types";
import { formatFigure } from "../lib/format";
import { StatusBadge } from "./primitives";

// U6 — "where does this number come from?", answered in place. Every filing figure
// gets a Source button that opens an overlay anchored to it (a bottom sheet on a
// phone), never a page-covering modal: the filing row the figure sits in, the value
// exactly as filed, and — beside it — what the agent cited and whether that matches.
// A web citation opens the snippet the agent actually read (recorded at the time,
// not re-fetched). Backed by BP: /api/facts/excerpt and /api/runs/{id}/source-snippet.

// ── What the page knows about the run's sources ────────────────────────────────

/** A lens concept -> the canonical metric the comparison rows use (validation/facts.py). */
export const CONCEPT_METRIC: Record<string, string> = {
  Assets: "total_assets",
  Revenues: "revenue",
  NetIncomeLoss: "net_income",
  EarningsPerShareDiluted: "eps_diluted",
  EarningsPerShareBasic: "eps_basic",
  OperatingIncomeLoss: "operating_income",
};

/** Plain names for the overlay title; the tag itself is kept in the filing link's context. */
const CONCEPT_LABEL: Record<string, string> = {
  Assets: "Total assets", Revenues: "Revenue", NetIncomeLoss: "Net income",
  EarningsPerShareDiluted: "Diluted EPS", EarningsPerShareBasic: "Basic EPS", OperatingIncomeLoss: "Operating income",
};

export interface Sources {
  runId: string;
  cik: string | null;
  facts: { a: Fact[]; b: Fact[] } | null;
  checks: Check[];
  /** Figures the gate is still withholding: no source is offered for them. */
  withheldMetrics: Set<string>;
  /** Each agent's cited figures, to mark in a web snippet. */
  cited: { a: string[]; b: string[] };
}

const SourceContext = createContext<Sources | null>(null);

export function sourcesOf(run: Run): Sources {
  const cmp = run.cross_agent_comparison;
  const rows = cmp?.metric_comparisons ?? [];
  const cik = (run.steps ?? []).find((s) => s.cik)?.cik ?? null;
  return {
    runId: run.run_id,
    cik,
    facts: run.facts ?? null,
    checks: cmp?.structural_flags?.checks ?? [],
    withheldMetrics: new Set(rows.filter((r) => r.withheld).map((r) => r.metric)),
    // The comparison rows' figures, plus each agent's extracted numbers — recorded even
    // when the run halted and there are no rows (found live on MSFT run 881a625e).
    cited: {
      a: [...new Set([...rows.map((r) => r.raw_a), ...(cmp?.agent_a_numbers ?? [])].filter((x): x is string => !!x))],
      b: [...new Set([...rows.map((r) => r.raw_b), ...(cmp?.agent_b_numbers ?? [])].filter((x): x is string => !!x))],
    },
  };
}

export function SourcesProvider({ value, children }: { value: Sources; children: ReactNode }) {
  return <SourceContext.Provider value={value}>{children}</SourceContext.Provider>;
}

export const useSources = () => useContext(SourceContext);

export function givenFact(sources: Sources | null, slot: "a" | "b", metric: string): Fact | null {
  const facts = sources?.facts?.[slot] ?? [];
  return facts.find((f) => !f.missing && CONCEPT_METRIC[f.concept] === metric) ?? null;
}

/** The claim-vs-source check (B3) for this agent's figure, if one ran. */
export function sourceCheck(sources: Sources | null, slot: "a" | "b", metric: string): Check | null {
  return sources?.checks.find((c) => c.key === `source:agent_${slot}:${metric}`) ?? null;
}

// ── The overlay shell: anchored, dismissible, focus-managed ────────────────────

function useOverlay() {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const close = () => { setOpen(false); button.current?.focus(); };
  useEffect(() => {
    if (!open) return;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); close(); } };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!panel.current?.contains(t) && !button.current?.contains(t)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onDown); };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  return { open, setOpen, close, button, panel };
}

function Overlay({ title, align, panelRef, onClose, children }: {
  title: string; align: "start" | "end"; panelRef: React.RefObject<HTMLDivElement>; onClose: () => void; children: ReactNode;
}) {
  const id = useId();
  return (
    <div ref={panelRef} className={`source-pop align-${align}`} role="dialog" aria-modal="false"
         aria-labelledby={`${id}-t`} tabIndex={-1}>
      <div className="source-pop-head">
        <strong id={`${id}-t`}>{title}</strong>
        <button type="button" className="info-btn" aria-label="Close" onClick={onClose}>✕</button>
      </div>
      {children}
    </div>
  );
}

function Marked({ text, span }: { text: string; span: [number, number] | null }) {
  if (!span) return <>{text}</>;
  return (
    <>
      {text.slice(0, span[0])}
      <mark><span className="sr-only">highlighted value: </span>{text.slice(span[0], span[1])}</mark>
      {text.slice(span[1])}
    </>
  );
}

// ── Filing figures ─────────────────────────────────────────────────────────────

const excerptCache = new Map<string, Promise<Excerpt>>();

function loadExcerpt(cik: string, fact: Fact): Promise<Excerpt> {
  const start = fact.period_kind === "instant" ? null : fact.start ?? null;
  const key = [cik, fact.accn, fact.concept, start, fact.end].join("|");
  let p = excerptCache.get(key);
  if (!p) {
    p = api.excerpt({ cik, accn: fact.accn!, concept: fact.concept, end: fact.end!, start });
    p.catch(() => excerptCache.delete(key)); // a failed request can be retried
    excerptCache.set(key, p);
  }
  return p;
}

function OccurrenceView({ o, ex }: { o: Occurrence; ex: Excerpt }) {
  return (
    <div className="occurrence">
      {o.in_table && (
        <p className="small">
          <span className="muted">Row</span> <strong>{o.row_label ?? "—"}</strong>
          {o.column_header && <> · <span className="muted">Column</span> {o.column_header}</>}
        </p>
      )}
      <p className="source-excerpt"><Marked text={o.excerpt} span={o.highlight} /></p>
      <p className="small">
        Value as filed: <span className="figure">{o.displayed}</span>
        {ex.scale_words && <> (in {ex.scale_words})</>}
        {o.value !== null && o.scale !== 0 && <> = <span className="figure">{formatFigure(o.value, "USD")}</span></>}
      </p>
    </div>
  );
}

export function FilingSource({ slot, metric, fact: explicit, cited, label = "Source" }: {
  slot: "a" | "b";
  metric?: string;
  fact?: Fact;
  /** The agent's own wording of the figure, shown beside the filed value. */
  cited?: string | null;
  label?: string;
}) {
  const sources = useSources();
  const ov = useOverlay();
  const [ex, setEx] = useState<Excerpt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [others, setOthers] = useState(false);
  const fact = explicit ?? (metric ? givenFact(sources, slot, metric) : null);
  const m = metric ?? (fact ? CONCEPT_METRIC[fact.concept] : undefined);
  const check = m ? sourceCheck(sources, slot, m) : null;

  useEffect(() => {
    if (!ov.open || ex || !sources?.cik || !fact) return;
    loadExcerpt(sources.cik, fact).then(setEx).catch((e: Error) => setError(e.message));
  }, [ov.open]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!sources?.cik || !fact?.accn || !fact.end) return null;          // nothing to look up
  if (m && sources.withheldMetrics.has(m)) return null;                  // the gate is withholding this figure

  const doc = fact.form ?? "filing";
  return (
    <span className="source-anchor">
      <button ref={ov.button} type="button" className="source-btn" aria-expanded={ov.open}
              aria-haspopup="dialog" onClick={() => ov.setOpen((o) => !o)}
              title={`Where this figure is in the ${doc}`}>
        {label}
      </button>
      {ov.open && (
        <Overlay title={`${CONCEPT_LABEL[fact.concept] ?? fact.concept} in the ${doc}${ex?.filed ? `, filed ${ex.filed}` : ""}`}
                 align={slot === "b" ? "end" : "start"} panelRef={ov.panel} onClose={ov.close}>
          {!ex && !error && <p className="skeleton small" aria-busy="true">Finding this figure in the {doc}…</p>}
          {error && <p className="error-text" role="alert">Couldn't look this up: {error}</p>}
          {ex && ex.status === "found" && ex.best && (
            <>
              <OccurrenceView o={ex.best} ex={ex} />
              {cited && (
                <p className="small cited-line">
                  Agent {slot.toUpperCase()} cited <span className="figure">{cited}</span>{" "}
                  {check && check.outcome !== "skipped" &&
                    <StatusBadge state={check.outcome === "pass" ? "source_match" : "source_mismatch"} />}
                </p>
              )}
              {ex.occurrence_count > 1 && (
                <>
                  <button type="button" className="btn-ghost small" aria-expanded={others} onClick={() => setOthers((o) => !o)}>
                    {others ? "Hide" : "Show"} {ex.occurrence_count - 1} other place{ex.occurrence_count > 2 ? "s" : ""} it appears
                  </button>
                  {others && ex.occurrences.slice(1).map((o, i) => <OccurrenceView key={o.fact_id ?? i} o={o} ex={ex} />)}
                  {others && ex.occurrence_count > ex.occurrences.length && (
                    <p className="muted small">…and {ex.occurrence_count - ex.occurrences.length} more in the filing.</p>
                  )}
                </>
              )}
            </>
          )}
          {ex && ex.status !== "found" && <p className="small">{ex.message}</p>}
          {(ex || error) && (
            <p className="small">
              <a href={ex?.document_url ?? ex?.filing_url ?? "#"} target="_blank" rel="noopener noreferrer">Open the filing</a>
              {ex?.period && <span className="muted"> · period {ex.period}</span>}
            </p>
          )}
        </Overlay>
      )}
    </span>
  );
}

// ── Web citations ──────────────────────────────────────────────────────────────

/** Spans of the agent's cited figures (by their numeric core, e.g. "94.9") in `text`. */
export function figureSpans(text: string, figures: string[]): [number, number][] {
  const spans: [number, number][] = [];
  for (const f of figures) {
    const core = f.match(/\d[\d,]*(?:\.\d+)?/)?.[0];
    if (!core) continue;
    const re = new RegExp(`(?<![\\d.,])${core.replace(/[.,]/g, (c) => `\\${c}`)}(?![\\d])`, "g");
    for (let m = re.exec(text); m; m = re.exec(text)) spans.push([m.index, m.index + m[0].length]);
  }
  return spans.sort((x, y) => x[0] - y[0]).filter((s, i, all) => i === 0 || s[0] >= all[i - 1][1]);
}

function MarkedMany({ text, spans }: { text: string; spans: [number, number][] }) {
  const out: ReactNode[] = [];
  let at = 0;
  spans.forEach(([s, e], i) => {
    out.push(text.slice(at, s));
    out.push(<mark key={i}><span className="sr-only">highlighted value: </span>{text.slice(s, e)}</mark>);
    at = e;
  });
  out.push(text.slice(at));
  return <>{out}</>;
}

export function WebSource({ url, label, slot }: { url: string; label: string; slot: "a" | "b" | null }) {
  const sources = useSources();
  const ov = useOverlay();
  const [sn, setSn] = useState<Snippet | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ov.open || sn || !sources) return;
    api.snippet(sources.runId, url).then(setSn).catch((e: Error) => setError(e.message));
  }, [ov.open]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!sources) {
    return <a href={url} target="_blank" rel="noopener noreferrer" className="cite-pill">{label}</a>;
  }
  const figures = slot ? sources.cited[slot] : [...sources.cited.a, ...sources.cited.b];
  return (
    <span className="source-anchor">
      <button ref={ov.button} type="button" className="cite-pill cite-btn" aria-expanded={ov.open}
              aria-haspopup="dialog" onClick={() => ov.setOpen((o) => !o)} title="What the agent read at this address">
        {label}
      </button>
      {ov.open && (
        <Overlay title={sn?.title || label} align="start" panelRef={ov.panel} onClose={ov.close}>
          {!sn && !error && <p className="skeleton small" aria-busy="true">Finding what the agent read here…</p>}
          {error && <p className="error-text" role="alert">Couldn't look this up: {error}</p>}
          {sn?.status === "found" && (
            <>
              {sn.query && <p className="muted small">Found by agent {(sn.agent ?? "").toUpperCase()} searching <q>{sn.query}</q></p>}
              {sn.snippet
                ? <p className="source-excerpt"><MarkedMany text={sn.snippet} spans={figureSpans(sn.snippet, figures)} /></p>
                : <p className="muted small">The search returned this address without any text.</p>}
              <p className="muted small">As recorded when the agent searched; the page may have changed since.</p>
            </>
          )}
          {sn && sn.status !== "found" && <p className="small">{sn.message}</p>}
          <p className="small"><a href={url} target="_blank" rel="noopener noreferrer">Open the page</a></p>
        </Overlay>
      )}
    </span>
  );
}
