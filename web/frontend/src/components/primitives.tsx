import { useId, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { GLOSSARY, type GlossaryKey } from "../lib/glossary";
import { formatFigure, formatPeriodChip } from "../lib/format";
import type { Fact } from "../api/types";

// ── One status vocabulary, used everywhere ─────────────────────────────────────
// Every state carries a text label and a glyph, so nothing depends on color alone.
// Colors come from the legacy palette's existing badge classes (styles.css).

export type StatusState =
  | "match" | "mismatch" | "different_periods" | "one_sided" | "unchecked"
  | "uncorroborated" | "unverifiable_period" | "derived_ok" | "derived_wrong"
  | "verified" | "not_found" | "ok" | "success" | "error" | "parse_failure"
  | "halted" | "retried" | "running" | "awaiting_decision" | "decided" | "withheld" | "cited_by_one"
  | "check_pass" | "check_fail" | "check_warn" | "check_skipped" | "source_match" | "source_mismatch";

const STATUS: Record<StatusState, { label: string; glyph: string; cls: string }> = {
  match:             { label: "Match",                 glyph: "✓", cls: "badge-success" },
  mismatch:          { label: "Mismatch",              glyph: "≠", cls: "badge-danger" },
  different_periods: { label: "Different periods",     glyph: "⚠", cls: "badge-warning" },
  one_sided:         { label: "Only one agent was given this", glyph: "·", cls: "badge-neutral" },
  uncorroborated:    { label: "Only one agent cited this",     glyph: "⚠", cls: "badge-warning" },
  unverifiable_period: { label: "Period stated by one agent only", glyph: "⚠", cls: "badge-warning" },
  derived_ok:        { label: "Recomputed — checks out",       glyph: "✓", cls: "badge-success" },
  derived_wrong:     { label: "Doesn't add up",                glyph: "≠", cls: "badge-danger" },
  unchecked:         { label: "Couldn't check",        glyph: "—", cls: "badge-neutral" },
  verified:          { label: "Matches source",        glyph: "✓", cls: "badge-success" },
  not_found:         { label: "Not found in source",   glyph: "✗", cls: "badge-danger" },
  ok:                { label: "OK",                    glyph: "✓", cls: "badge-success" },
  success:           { label: "Passed format check",   glyph: "✓", cls: "badge-success" },
  error:             { label: "Error",                 glyph: "✗", cls: "badge-danger" },
  parse_failure:     { label: "Failed format check",   glyph: "⚠", cls: "badge-warning" },
  halted:            { label: "Halted",                glyph: "⛔", cls: "badge-halt" },
  retried:           { label: "Retried",               glyph: "↻", cls: "badge-warning" },
  running:           { label: "Running",               glyph: "…", cls: "badge-neutral" },
  awaiting_decision: { label: "Needs decision",        glyph: "⚑", cls: "badge-warning" },
  decided:           { label: "Decided",               glyph: "✓", cls: "badge-success" },
  withheld:          { label: "Pending human review",  glyph: "⏸", cls: "badge-neutral" },
  cited_by_one:      { label: "Both were given this; one cited it", glyph: "·", cls: "badge-neutral" },
  check_pass:        { label: "Adds up",               glyph: "✓", cls: "badge-success" },
  check_fail:        { label: "Doesn't add up",        glyph: "≠", cls: "badge-danger" },
  check_warn:        { label: "Unusual, worth a look", glyph: "⚠", cls: "badge-warning" },
  check_skipped:     { label: "Not checked",           glyph: "—", cls: "badge-neutral" },
  source_match:      { label: "Matches the filing",    glyph: "✓", cls: "badge-success" },
  source_mismatch:   { label: "Doesn't match the filing", glyph: "≠", cls: "badge-danger" },
};

export function StatusBadge({ state, children }: { state: StatusState; children?: ReactNode }) {
  const s = STATUS[state];
  return (
    <span className={`badge ${s.cls}`} data-state={state}>
      <span aria-hidden="true">{s.glyph}</span> {children ?? s.label}
    </span>
  );
}

// ── Who authored a piece of the record ─────────────────────────────────────────

export type Role = "user" | "agent" | "verifier";
const ROLE_LABEL: Record<Role, string> = { user: "User", agent: "Agent", verifier: "Verification Layer" };

export function RolePill({ role }: { role: Role }) {
  return <span className={`role-pill role-${role}`}>{ROLE_LABEL[role]}</span>;
}

// ── Plain term with the technical one a click away ─────────────────────────────

export function Info({ term }: { term: GlossaryKey }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const g = GLOSSARY[term];
  return (
    <span className="info">
      <button
        type="button"
        className="info-btn"
        aria-expanded={open}
        aria-controls={id}
        aria-label={`What does "${g.plain}" mean?`}
        onClick={() => setOpen((o) => !o)}
      >
        ⓘ
      </button>
      {open && (
        <span id={id} role="note" className="info-note">
          <code>{g.technical}</code> — {g.definition}
        </span>
      )}
    </span>
  );
}

// ── Model text: rendered as Markdown with raw HTML disabled ────────────────────
// react-markdown never uses dangerouslySetInnerHTML and ignores embedded HTML by
// default, closing the innerHTML path the legacy renderMd() used.

export function Markdown({ text }: { text: string | null | undefined }) {
  if (!text) return null;
  return (
    <div className="md">
      <ReactMarkdown>{text}</ReactMarkdown>
    </div>
  );
}

// ── A reported figure with its period ──────────────────────────────────────────

export function FactValue({ fact }: { fact: Fact }) {
  if (fact.missing) return <span className="muted">not reported</span>;
  const chip = formatPeriodChip(fact);
  return (
    <span className="fact">
      <span className="figure" title={`${fact.value} ${fact.unit ?? ""} — ${fact.period_label ?? ""}`.trim()}>
        {formatFigure(fact.value, fact.unit)}
      </span>
      {chip ? <span className="period-chip">{chip}</span> : <span className="period-chip muted">Period not reported</span>}
    </span>
  );
}

export function Section({
  title, role, badge, children, defaultOpen = true,
}: {
  title: string; role?: Role; badge?: ReactNode; children: ReactNode; defaultOpen?: boolean;
}) {
  return (
    <details className="section" open={defaultOpen}>
      <summary>
        {role && <RolePill role={role} />}
        <span className="section-title">{title}</span>
        {badge}
      </summary>
      <div className="section-body">{children}</div>
    </details>
  );
}
