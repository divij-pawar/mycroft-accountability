import { useState } from "react";
import type { MetricComparison, MetricStatus } from "../api/types";
import { formatFigure } from "../lib/format";
import { StatusBadge, type StatusState } from "./primitives";
import { FilingSource, sourceCheck, useSources } from "./SourcePopover";

// Figure by figure: what each agent said about the same metric, with the verdict
// for that one figure sitting *between* the two values — the difference and the
// status are one column, so the eye goes A → Δ → B. Rows arrive severity-sorted
// from validation/facts.py. Figures only one agent was *given* (its lens) are
// folded away: they are expected, and must never read as a disagreement.

const STATE: Record<MetricStatus, StatusState> = {
  MATCH: "match",
  MISMATCH: "mismatch",
  DIFFERENT_PERIODS: "different_periods",
  UNVERIFIABLE_PERIOD: "unverifiable_period",
  ONE_SIDED: "one_sided",
  UNCORROBORATED: "uncorroborated",
  DERIVED_OK: "derived_ok",
  DERIVED_WRONG: "derived_wrong",
  CITED_BY_ONE: "cited_by_one",
};

const NEEDS_ATTENTION = new Set<MetricStatus>([
  "MISMATCH", "UNCORROBORATED", "DERIVED_WRONG", "DIFFERENT_PERIODS", "UNVERIFIABLE_PERIOD",
]);
const CONFLICTS = new Set<MetricStatus>(["MISMATCH", "DERIVED_WRONG"]);

type Filter = "all" | "attention" | "conflicts";

const UNIT: Record<string, string | null> = {
  currency: "USD", per_share: "USD/shares", percent_change: null, derived: null, year: null, untagged: null,
};

/** A value as a reader wants it; the agent's own wording is kept in the tooltip. */
function Value({ row, side }: { row: MetricComparison; side: "a" | "b" }) {
  const sources = useSources();
  const raw = side === "a" ? row.raw_a : row.raw_b;
  const value = side === "a" ? row.value_a : row.value_b;
  if (row.withheld) {
    return <span className="muted withheld" title="Withheld until a reviewer decides which figure is right">Withheld</span>;
  }
  if (raw === null || value === null) return <span className="muted">—</span>;
  const unit = UNIT[row.family];
  const shown = unit ? formatFigure(value, unit) : raw;
  // U5: whether this figure matches the filing value the agent was given, where
  // the reviewer is already reading; U6: where that filing value sits.
  const check = sourceCheck(sources, side, row.metric);
  return (
    <span className="cell-value">
      <span className="figure" title={`As agent ${side.toUpperCase()} wrote it: ${raw}`}>{shown}</span>
      {check && check.outcome !== "skipped" && (
        <StatusBadge state={check.outcome === "pass" ? "source_match" : "source_mismatch"} />
      )}
      <FilingSource slot={side} metric={row.metric} cited={raw} />
    </span>
  );
}

function Period({ row }: { row: MetricComparison }) {
  const { period_a: a, period_b: b } = row;
  if (!a && !b) return <span className="muted small">not stated</span>;
  if (a === b || !a || !b) return <span className="period-chip">{a ?? b}</span>;
  return (
    <span className="period-pair">
      <span className="period-chip">A: {a}</span>
      <span className="period-chip">B: {b}</span>
    </span>
  );
}

/** How far apart the two values are, in the unit a reader thinks in: a percentage
 * for amounts, but years apart for a year — "0.1%" for 1996 vs 1998 is true and useless. */
function gap(row: MetricComparison): string | null {
  if (row.family === "year" && row.value_a !== null && row.value_b !== null) {
    const years = Math.abs(row.value_a - row.value_b);
    return years ? `${years} year${years === 1 ? "" : "s"}` : null;
  }
  return row.variance_pct !== null && row.variance_pct > 0 ? `${row.variance_pct}%` : null;
}

function Delta({ row }: { row: MetricComparison }) {
  const state = STATE[row.status];
  const shown = gap(row);
  return (
    <div className="delta">
      {shown && <span className="delta-pct">{shown}</span>}
      {row.status === "MISMATCH" && shown
        ? <StatusBadge state="mismatch">Mismatch {shown}</StatusBadge>
        : <StatusBadge state={state} />}
      {row.withheld && <StatusBadge state="withheld" />}
    </div>
  );
}

function Row({ row }: { row: MetricComparison }) {
  return (
    <>
      <tr className={`mx-row mx-${row.status.toLowerCase()}`} data-status={row.status}>
        <th scope="row" className="mx-figure">{row.label}</th>
        <td className="mx-period" data-label="Period"><Period row={row} /></td>
        <td className="mx-a" data-label="Agent A"><Value row={row} side="a" /></td>
        <td className="mx-delta" data-label="Difference"><Delta row={row} /></td>
        <td className="mx-b" data-label="Agent B"><Value row={row} side="b" /></td>
      </tr>
      {row.note && (
        <tr className="mx-note-row">
          <td colSpan={5} className="mx-note">{row.note}</td>
        </tr>
      )}
    </>
  );
}

function Table({ rows, caption }: { rows: MetricComparison[]; caption: string }) {
  return (
    <table className="matrix">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Figure</th>
          <th scope="col">Period</th>
          <th scope="col" className="mx-a">Agent A</th>
          <th scope="col" className="mx-delta">Difference</th>
          <th scope="col" className="mx-b">Agent B</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => <Row key={`${r.metric}-${i}`} row={r} />)}
      </tbody>
    </table>
  );
}

export function ComparisonMatrix({ rows }: { rows: MetricComparison[] }) {
  const [filter, setFilter] = useState<Filter>("all");
  const oneSided = rows.filter((r) => r.status === "ONE_SIDED");
  const main = rows.filter((r) => r.status !== "ONE_SIDED");
  const visible = main.filter((r) =>
    filter === "all" ? true : filter === "attention" ? NEEDS_ATTENTION.has(r.status) : CONFLICTS.has(r.status));
  const count = (f: Filter) =>
    main.filter((r) => (f === "all" ? true : f === "attention" ? NEEDS_ATTENTION.has(r.status) : CONFLICTS.has(r.status))).length;

  if (!rows.length) {
    return <p className="muted">Neither agent cited a figure, so there is nothing to compare.</p>;
  }

  return (
    <div className="matrix-wrap">
      {main.length > 0 && (
        <>
          <div className="trace-toolbar matrix-filters" role="group" aria-label="Filter figures">
            {([["all", "All"], ["attention", "Needs attention"], ["conflicts", "Conflicts"]] as const).map(([f, label]) => (
              <button key={f} type="button" className={`chip ${filter === f ? "chip-active" : ""}`}
                      aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {label} <span className="count">{count(f)}</span>
              </button>
            ))}
          </div>
          {visible.length
            ? <Table rows={visible} caption="Figures both agents cited, or that only one agent cited" />
            : <p className="muted small">No figures in this view.</p>}
        </>
      )}
      {oneSided.length > 0 && (
        <details className="mx-one-sided" open={main.length === 0}>
          <summary>
            Figures only one agent was given ({oneSided.length})
            <span className="muted small"> — expected: each agent reads a different part of the filing</span>
          </summary>
          <Table rows={oneSided} caption="Figures only one agent was given" />
        </details>
      )}
    </div>
  );
}

/** Headline counts for the summary card, from the rows (not from the recorded flag). */
export function matrixCounts(rows: MetricComparison[]) {
  const n = (s: MetricStatus) => rows.filter((r) => r.status === s).length;
  const shared = rows.filter((r) => ["MATCH", "MISMATCH", "DIFFERENT_PERIODS", "UNVERIFIABLE_PERIOD"].includes(r.status)).length;
  return {
    shared,
    matched: n("MATCH"),
    mismatched: n("MISMATCH"),
    uncorroborated: n("UNCORROBORATED"),
    derivedWrong: n("DERIVED_WRONG"),
    flagged: rows.some((r) => r.flags),
  };
}
