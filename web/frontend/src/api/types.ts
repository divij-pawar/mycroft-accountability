// Hand-mirrored from the Python payloads (web/server.py _run_chat / _run_compare,
// core/schemas.py ReasoningObject.to_dict, validation/claims.py ExtractedClaim.to_dict,
// web/step_trace.py steps, datasources/edgar.py Fact.to_dict). Optional fields are
// optional because stored runs predate them — every renderer must tolerate absence.
// tests/contract.test.ts parses real stored payloads against these shapes.

export type Scope = "auditor" | "investor";

export type ParseStatus = "SUCCESS" | "PARSE_FAILURE" | "HALT";

export interface Step {
  seq: number;
  phase: string; // shared | agent_a | agent_b | compare | chat
  label: string;
  started_at?: string;
  detail: string | null;
  url: string | null;
  status: string; // ok | error | parse_failure
  error: string | null;
  duration_ms: number | null;
  kind?: "fetch" | "llm" | "tool" | "compare" | "extract"; // extract: B4 option 1's grade-reading call
  attempt?: number;
  model?: string;
  tool?: string;
  tool_phase?: "started" | "finished";
  query?: string | null;
  args?: Record<string, unknown>;
  retried_query_only?: boolean;
  urls?: string[];
  /** BP (2026-09-25+): what each search result said, as the agent read it. */
  results?: { url: string; title: string | null; snippet: string | null }[];
  /** Investor scope, gate open: what the search returned is withheld (validation/gate.py). */
  result_withheld?: boolean;
  cik?: string;
}

export interface ContextWindow {
  subject: string;
  context: string;
}

// SEC-01: at investor scope thought_log / raw_output / directive_text / context_window
// are structurally omitted (the key is absent), not nulled.
export interface ReasoningObject {
  reasoning_id: string;
  run_id: string;
  agent_id: string;
  attempt_number: number;
  parse_status: ParseStatus;
  confidence_score: number;
  conclusion?: string | null;
  thought_log?: string | null;
  raw_output?: { text?: string } | null;
  directive_version?: string;
  directive_text?: string;
  context_window?: ContextWindow;
  created_at?: string;
  // B4 (directive v1.6.0+) — INTERNAL TIER: absent (the key omitted) at investor scope.
  assessment?: Record<string, unknown> | null;
  assessment_status?: "valid" | "partial" | "invalid_fields" | "invalid_json" | "unclosed" | "empty" | "absent" | null;
  assessment_issues?: string[];
}

export interface Claim {
  claim_type: "citation" | "quantitative" | "hedge" | "causal";
  text: string;
  context: string;
  verified: boolean | null;
  source_label?: string;
  source_url?: string;
}

export interface DataSource {
  source: string;
  status: string;
  url?: string | null;
  provenance_note?: string | null;
}

export interface Session {
  run_id: string;
  ticker: string;
  status: string;
  directive_version: string;
  directive_text?: string;
  initiated_at: string;
  completed_at?: string | null;
  run_confidence_score?: number | null;
  confidence_classification?: string | null;
  reasoning_objects?: ReasoningObject[];
}

export interface Consistency {
  agreement: "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN";
  score: number;
  probe_error?: string | null;
  divergent_numbers?: string[];
}

export interface Fact {
  concept: string;
  missing?: boolean;
  tag?: string;
  value?: number;
  unit?: string | null;
  start?: string | null;
  end?: string | null;
  fy?: number | null;
  fp?: string | null;
  form?: string | null;
  frame?: string | null;
  accn?: string | null;
  filed?: string | null;
  period_kind?: "instant" | "quarter" | "annual" | "ytd" | "unknown";
  period_label?: string;
}

/** One row of validation/facts.py's figure-by-figure comparison (B1). */
export type MetricStatus =
  | "MATCH" | "MISMATCH" | "DIFFERENT_PERIODS" | "UNVERIFIABLE_PERIOD"
  | "ONE_SIDED" | "UNCORROBORATED" | "DERIVED_OK" | "DERIVED_WRONG" | "CITED_BY_ONE";

export interface MetricComparison {
  metric: string;
  label: string;
  family: "currency" | "per_share" | "percent_change" | "derived" | "year" | "untagged";
  status: MetricStatus;
  value_a: number | null;
  value_b: number | null;
  raw_a: string | null;
  raw_b: string | null;
  period_a: string | null;
  period_b: string | null;
  variance_pct: number | null;
  note: string | null;
  flags: boolean;
  /** Investor scope, gate awaiting a decision: the values are withheld (validation/gate.py). */
  withheld?: boolean;
}

export interface Comparison {
  run_id: string;
  subject: string;
  agent_a_id: string;
  agent_b_id: string;
  status: "COMPARED" | "AGENT_A_HALTED" | "AGENT_B_HALTED" | "BOTH_HALTED";
  agent_a_conclusion: string | null;
  agent_b_conclusion: string | null;
  agent_a_numbers: string[];
  agent_b_numbers: string[];
  divergent_numbers: string[];
  contradiction_flag: boolean | null;
  word_overlap: number | null;
  number_overlap: number | null;
  score: number | null;
  agreement: string | null;
  compared_at: string;
  // Absent on runs stored before 2026-09-24 — renderers fall back to divergent_numbers.
  metric_comparisons?: MetricComparison[] | null;
  contradiction_rule?: string;
  // B3 — absent on runs stored before 2026-09-25
  structural_flags?: StructuralFlags | null;
  // B5 — absent before 2026-09-26; at investor scope only the kind of disagreement
  synthesis?: Synthesis | null;
}

export type ConflictDriver = "data" | "assumption" | "weighting" | "none" | "insufficient";

export interface GradeCandidate {
  slot: "a" | "b";
  grade: string | null;
  direction: "buy" | "hold" | "sell" | null;
  assumptions: Record<string, number | string>;
  evidence: { match_filing: number; contradict_filing: number; unchecked: number; unbacked: number; hard_check_failures: number };
}

/** B5: validation/divergence.py synthesize(). */
export interface Synthesis {
  policy: string;
  primary_conflict_driver: ConflictDriver;
  data_conflicts?: string[];
  assumption_differences?: { key: string; a: number | string; b: number | string }[];
  grade_candidates?: GradeCandidate[];
  consensus_grade?: { grade: string; direction: string } | null;
  consensus_reason?: string | null;
  audit_recommendation?: string;
  needs_decision?: boolean;
  /** Investor scope: grades are model judgments and stay internal. */
  withheld?: boolean;
}

/** B3: validation/constraints.py CheckResult.to_dict(). */
export interface Check {
  key: string;
  rule: string; // a rule id, or "matches_source"
  kind: "hard" | "heuristic";
  scope: "agent_a" | "agent_b" | "source";
  who: string;
  outcome: "pass" | "fail" | "skipped";
  plain: string;
  math: string | null;
  reason: string | null;
  metrics: string[];
  gates: boolean;
  withheld?: boolean;
}

export interface StructuralFlags {
  policy: string;
  checks: Check[];
  gating: string[];
}

export interface Producer {
  agent_id: string;
  role: string;
  model: string;
  concepts: string[];
  overridden: boolean;
  lens_version?: string; // B2: "v2" shares NetIncomeLoss + EarningsPerShareDiluted
  lens?: string;         // B4: financial | earnings | bull | bear
}

/** A stored run as returned by /api/runs and /api/runs/{id}: chat or compare. */
export interface Run {
  run_id: string;
  subject?: string | null;
  ticker?: string | null;
  scope?: Scope;
  halted: boolean;
  error: string | null;
  session?: Session | null;
  reasoning_objects: ReasoningObject[];
  steps?: Step[];
  tool_capability_warning?: string | null;
  // chat
  conclusion?: string | null;
  thought_log?: string | null;
  confidence_score?: number;
  confidence_classification?: string;
  high_uncertainty?: boolean;
  data_sources?: DataSource[];
  claims?: Claim[] | { a: Claim[]; b: Claim[] };
  verification_rate?: number | null | { a: number | null; b: number | null };
  consistency?: Consistency | null;
  config_snapshot?: Record<string, unknown>;
  // compare
  cross_agent_comparison?: Comparison | null;
  producers?: { a: Producer; b: Producer; same_model: boolean; shared_concepts?: string[]; pairing?: "lenses" | "bull_bear" };
  contexts?: { a: string | null; b: string | null };
  facts?: { a: Fact[]; b: Fact[] };
  // BG — absent on runs stored before the gate (and on chat runs)
  gate_policy?: string;
  gate?: Gate;
  withheld_pending_review?: string;
}

/** BG: validation/gate.py gate_state(). */
export type GateStatus = "NOT_GATED" | "NO_DECISION_NEEDED" | "AWAITING_DECISION" | "DECIDED";
export type DecisionKind =
  | "accept_a" | "accept_b" | "both_wrong" | "not_a_conflict" | "override_value" | "confirmed_error" | "set_grade";

export interface GateDecision {
  decision_id: string;
  run_id: string;
  decided_by: string;
  decision: DecisionKind;
  final_value: number | null;
  final_grade?: string | null;
  rationale: string;
  cited_items: string[];
  decided_at: string;
  superseded?: boolean;
}

/** One thing needing a decision: a disputed figure (B1) or a failed hard check (B3). */
export interface GateItem {
  metric: string; // the key decisions cite
  kind?: "figure" | "check" | "grade"; // absent on policy-v1 gates, which only had figures
  label: string;
  status: string;
  decision_id: string | null;
  metrics?: string[];
  scope?: string;
  math?: string | null;
  withheld?: boolean;
  /** kind "grade" (B5): each agent's grade — auditor scope only. */
  candidates?: Record<"a" | "b", { grade: string | null; direction: string | null; assumptions: Record<string, number | string> }>;
  driver?: ConflictDriver;
}

export interface Gate {
  status: GateStatus;
  policy: string | null;
  identity_note: string;
  items: GateItem[];
  pending: string[];
  decisions: GateDecision[]; // newest first
  /** The grade a named human recorded — the only grade investors are shown (B5). */
  decided_grade?: { grade: string; decided_by: string; decided_at: string; decision_id: string } | null;
}

export interface DecisionInput {
  decision: DecisionKind;
  decided_by: string;
  rationale: string;
  cited_items: string[];
  final_value: number | null;
  final_grade?: string | null;
}

/** BP: datasources/filings.py Excerpt.to_dict(). */
export interface Occurrence {
  fact_id: string | null;
  context: string;
  displayed: string;
  value: number | null;
  scale: number;
  in_table: boolean;
  row_label: string | null;
  column_header: string | null;
  excerpt: string;
  highlight: [number, number] | null;
}

export interface Excerpt {
  status: "found" | "not_found" | "no_inline_xbrl" | "too_large" | "fetch_failed" | "not_in_index";
  filing_url: string;
  document_url: string | null;
  form: string | null;
  filed: string | null;
  concept: string | null;
  tag: string | null;
  period: string | null;
  occurrences: Occurrence[];
  occurrence_count: number;
  scale_words: string | null;
  message: string | null;
  best: Occurrence | null;
}

/** BP: GET /api/runs/{id}/source-snippet. */
export interface Snippet {
  status: "found" | "not_found" | "withheld"; // withheld: investor scope, gate still open
  url: string;
  title?: string | null;
  snippet?: string;
  highlight?: [number, number] | null;
  query?: string | null;
  agent?: string | null;
  message?: string;
}

/** GET /api/self-report — the Honest Ledger (web/self_report.py). */
export interface KnownIssue {
  id: string;
  severity: "critical" | "high" | "medium" | "low" | "info";
  area: string;
  title: string;
  detail: string;
  status: "OPEN" | "UNVERIFIED" | "RESOLVED" | "BY_DESIGN";
  source: string;
}

export interface SelfReport {
  automated_tests: { total: number | null; modules: { module: string; tests: number }[]; error: string | null };
  live_model_tests: { run_on: string; model: string; caveats: string[]; tests: { n: number; name: string; status: string; outcome?: string }[] };
  known_issues: KnownIssue[];
  counts: { issues_total: number; issues_open: number; issues_critical: number };
  deployment_status: { state: string; detail: string };
}

export interface Flag {
  flag_id: string;
  run_id: string;
  flag_type: "Hallucinated" | "Incorrect" | "Other";
  reviewer_note: string | null;
  flagged_at: string;
}

export const isCompareRun = (run: Run): boolean => "cross_agent_comparison" in run;
