// Plain language first, technical terms on demand: every jargon term the UI shows
// in a tooltip maps here to the plain phrase used in primary copy and a one-line
// definition. The technical term is never deleted — it is one click away.

export interface GlossaryEntry {
  plain: string;
  technical: string;
  definition: string;
}

export const GLOSSARY = {
  thought_log: {
    plain: "Reasoning",
    technical: "thought_log",
    definition:
      "The agent's own account of how it reached its conclusion. Well-formed does not mean true (ADR-06).",
  },
  context_window: {
    plain: "What the agent was sent",
    technical: "context window (directive + subject + context)",
    definition: "The exact system prompt and user prompt for each attempt, as the validation loop recorded them.",
  },
  adr07: {
    plain: "Format check",
    technical: "ADR-07 structural validation",
    definition:
      "Every response must be exactly two blocks. A failure is retried once with a corrective prompt; a second failure halts the run.",
  },
  sec01: {
    plain: "Investor view",
    technical: "SEC-01 scope tier",
    definition:
      "At investor scope the reasoning, raw output and prompts are structurally left out of the response — not blanked, absent.",
  },
  contradiction_null: {
    plain: "No comparison was possible",
    technical: "contradiction_flag = null",
    definition:
      "At least one agent never produced a conclusion, so nothing was compared. Recorded differently from \"compared and found nothing\".",
  },
  verified_null: {
    plain: "Couldn't check",
    technical: "verified = null",
    definition:
      "The cited source could not be fetched or parsed, so the claim was never checked either way — not the same as checked and wrong.",
  },
  score: {
    plain: "Similarity score",
    technical: "score / word_overlap / number_overlap",
    definition:
      "How alike the two conclusions' words and numbers are. A heuristic, not agreement on substance.",
  },
  gate: {
    plain: "Needs your decision",
    technical: "gate.status = AWAITING_DECISION (validation/gate.py)",
    definition:
      "The agents gave different values for the same figure. The run stays open until a named reviewer records which is right for every such figure; until then investors see \"pending human review\" instead of the figures.",
  },
} satisfies Record<string, GlossaryEntry>;

export type GlossaryKey = keyof typeof GLOSSARY;
