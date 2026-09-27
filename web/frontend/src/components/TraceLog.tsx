import { useMemo, useState } from "react";
import type { Step } from "../api/types";
import { formatClock, formatDuration } from "../lib/format";
import { RolePill, StatusBadge, type StatusState } from "./primitives";

// Terminal-style, chronological record of everything a run did: the shared EDGAR
// fetch, every LLM attempt, every tool call. Collapsed by default so it never
// competes with the answer — the one-line summary still shows the latest step.

const PHASE_LABEL: Record<string, string> = {
  shared: "shared",
  agent_a: "agent A",
  agent_b: "agent B",
  compare: "compare",
  chat: "agent",
};

/** Hostname for a pill label; URLs come from model/tool output, so never assume they parse. */
export function hostOf(url: string): string {
  try {
    return new URL(url).hostname || url;
  } catch {
    return url;
  }
}

function stepState(step: Step): StatusState {
  if (step.status === "parse_failure") return "parse_failure";
  if (step.status === "error") return "error";
  if (step.kind === "tool" && step.retried_query_only) return "retried";
  return "ok";
}

export function stepLine(step: Step): string {
  const what =
    step.kind === "tool"
      ? `tool ${step.tool ?? ""} ${step.tool_phase ?? ""}${step.query ? ` query=${JSON.stringify(step.query)}` : ""}`
      : step.label;
  const dur = formatDuration(step.duration_ms);
  return `${formatClock(step.started_at)}  ${(PHASE_LABEL[step.phase] ?? step.phase).padEnd(8)}  ${what}${dur ? `  ${dur}` : ""}  ${step.status}`;
}

export function TraceLog({ steps }: { steps: Step[] | undefined }) {
  const [phase, setPhase] = useState<string>("all");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);

  const ordered = useMemo(() => [...(steps ?? [])].sort((a, b) => a.seq - b.seq), [steps]);
  const phases = useMemo(() => Array.from(new Set(ordered.map((s) => s.phase))), [ordered]);
  const visible = phase === "all" ? ordered : ordered.filter((s) => s.phase === phase);

  if (!ordered.length) return null;
  const latest = ordered[ordered.length - 1];

  const copy = async () => {
    await navigator.clipboard?.writeText(visible.map(stepLine).join("\n"));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <details className="trace">
      <summary>
        <RolePill role="verifier" />
        <span className="section-title">Run trace</span>
        <span className="badge-neutral">{ordered.length} steps</span>
        <code className="trace-latest" aria-label="Latest step">{stepLine(latest)}</code>
      </summary>

      <div className="trace-toolbar" role="group" aria-label="Filter trace by lane">
        {["all", ...phases].map((p) => (
          <button
            key={p}
            type="button"
            className={`chip ${phase === p ? "chip-active" : ""}`}
            aria-pressed={phase === p}
            onClick={() => setPhase(p)}
          >
            {p === "all" ? "All" : PHASE_LABEL[p] ?? p}
          </button>
        ))}
        <button type="button" className="chip" onClick={copy}>
          {copied ? "Copied" : "Copy log"}
        </button>
      </div>

      <ol className="trace-lines">
        {visible.map((s) => {
          const open = expanded === s.seq;
          return (
            <li key={s.seq} className={`trace-line trace-${s.phase}`}>
              <button
                type="button"
                className="trace-row"
                aria-expanded={open}
                onClick={() => setExpanded(open ? null : s.seq)}
              >
                <code>{stepLine(s)}</code>
                <StatusBadge state={stepState(s)} />
              </button>
              {open && (
                <div className="trace-detail">
                  {s.detail && <pre>{s.detail}</pre>}
                  {s.result_withheld && (
                    <p className="scope-notice small">What this search returned is withheld: pending human review.</p>
                  )}
                  {s.error && <pre className="error-text">{s.error}</pre>}
                  {s.url && (
                    <a className="cite-pill" href={s.url} target="_blank" rel="noopener noreferrer">
                      {s.url}
                    </a>
                  )}
                  {!!s.urls?.length && (
                    <div className="cite-pills" aria-label="Result URLs">
                      {s.urls.map((u) => (
                        <a key={u} className="cite-pill" href={u} target="_blank" rel="noopener noreferrer">
                          {hostOf(u)}
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </details>
  );
}
