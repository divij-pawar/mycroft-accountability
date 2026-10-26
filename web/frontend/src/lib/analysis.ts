// How an analysis record is worded on screen. The letters are a display alias of the stored
// five-point signal (financial_agent/PLAN.md 6.2, decided 2026-10-06): A is the most positive.
import type { AnalysisOutput, AnalysisRecord, AnalysisStep, EvidenceRef, FigureCheck, FigureStatus, MeasurementsResponse, Tier1 } from "../api/types";

export const SIGNAL_LETTERS: Record<AnalysisOutput["signal"], string> = {
  strong_buy: "A", buy: "B", hold: "C", sell: "D", strong_sell: "E",
};
export const SIGNAL_WORDS: Record<AnalysisOutput["signal"], string> = {
  strong_buy: "Strong buy", buy: "Buy", hold: "Hold", sell: "Sell", strong_sell: "Strong sell",
};
export const HORIZON_WORDS: Record<AnalysisOutput["horizon"], string> = {
  "1-3m": "1–3 months", "3-6m": "3–6 months", "6-12m": "6–12 months", "12m+": "over 12 months",
};

/** `B · Buy · 3–6 months (default horizon) · medium conviction` */
export function headline(record: Pick<AnalysisRecord, "output" | "request">): string {
  const out = record.output;
  if (!out) return "No signal";
  const horizon = HORIZON_WORDS[out.horizon] + (record.request.horizon_defaulted ? " (default horizon)" : "");
  return [SIGNAL_LETTERS[out.signal], SIGNAL_WORDS[out.signal], horizon,
          out.conviction ? `${out.conviction} conviction` : null].filter(Boolean).join(" · ");
}

/** Where a reason says its figure came from, in words. Nothing here says the reference was checked. */
export function evidenceLabel(ref: EvidenceRef, tools: AnalysisRecord["tool_calls"]): string {
  if ("call" in ref) {
    const tool = tools.find((c) => c.seq === ref.call)?.tool;
    return tool ? `call ${ref.call} · ${tool}` : `call ${ref.call}`;
  }
  if ("chunk" in ref) return "your document";
  return "judgment";
}

/** One line of the progress list. A retry is said to be one. */
export function stepLine(step: AnalysisStep): string {
  switch (step.kind) {
    case "data": return step.detail ? `${step.label}: ${step.detail}` : step.label;
    case "tool": return step.status === "error"
      ? `Asked for ${step.label}: refused (${step.detail ?? "error"})`
      : `Looked at ${step.label}`;
    case "llm": return step.status === "parse_failure"
      ? `Attempt ${step.attempt}: the reply broke the format${step.detail ? ` (${step.detail})` : ""}`
      : `Attempt ${step.attempt}: the reply passed the format check`;
    default: return step.status === "halt"
      ? `Stopped: no signal was recorded${step.detail ? ` (${step.detail})` : ""}`
      : step.label;
  }
}

export const STEP_MARK: Record<AnalysisStep["status"], { mark: string; word: string }> = {
  ok: { mark: "✓", word: "done" },
  error: { mark: "✗", word: "refused" },
  warn: { mark: "!", word: "note" },
  parse_failure: { mark: "↻", word: "retry" },
  halt: { mark: "■", word: "halted" },
};

// ── Tier 1: were the figures quoted correctly? ──────────────────────────────────

export const FIGURE_MARK: Record<FigureStatus, { mark: string; word: string }> = {
  GROUNDED: { mark: "✓", word: "matches the data" },
  MISMATCH: { mark: "✗", word: "contradicts the data" },
  UNREFERENCED: { mark: "?", word: "not found in the data" },
  USER_ASSERTED: { mark: "◇", word: "from your document, not checked" },
  JUDGMENT: { mark: "~", word: "a judgment" },
};

/** `grounded 2/3`, or why there is nothing to count. */
export function groundedLabel(t: Tier1): string {
  return t.total === 0 ? "no figures to check" : `grounded ${t.grounded}/${t.total}`;
}

/** The badge's tone: only a clean, non-empty result is a success; a contradiction is the loud one. */
export function groundedTone(t: Tier1): "success" | "danger" | "warning" | "neutral" {
  if (t.mismatched > 0) return "danger";
  if (t.unreferenced > 0) return "warning";
  return t.total > 0 ? "success" : "neutral";
}

/** A figure written the way a chip shows it: `rsi_14 53.78 index`. */
export function figureText(c: Pick<FigureCheck, "name" | "value" | "unit">): string {
  return `${c.name} ${c.value}${c.unit ? ` ${c.unit}` : ""}`;
}

// ── What has been measured about the model ──────────────────────────────────────

/** One sentence on how the model has actually done on the reply format, or that nobody measured it. */
export function measurementLine(m: MeasurementsResponse): string {
  const mode = m.reasoning === "native" ? "thinks natively" : "no native reasoning";
  const head = `Model ${m.model} · ${mode}`;
  const s = m.measurement;
  if (!s) return `${head} · format reliability not measured.`;
  const day = s.measured_at.slice(0, 10);
  const older = s.current_prompt ? "" : ` Measured under an older prompt (${s.prompt_version}), not the one in use.`;
  const n = (count: number, noun: string) => `${count} ${noun}${count === 1 ? "" : "s"}`;
  const withSignal = s.first_attempt_passed + s.recovered_on_retry;
  return `${head} · in ${n(s.runs, "measured run")} its first reply followed the format ${n(s.first_attempt_passed, "time")}, `
    + `and ${n(withSignal, "run")} ended with a signal after the one retry `
    + `(${day}, ${s.tickers.join(", ")}).${older}`;
}
