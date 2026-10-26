// How an analysis record is worded on screen. The letters are a display alias of the stored
// five-point signal (financial_agent/PLAN.md 6.2, decided 2026-10-06): A is the most positive.
import type { AnalysisOutput, AnalysisRecord, AnalysisStep, EvidenceRef } from "../api/types";

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
