import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ComparisonMatrix } from "../src/components/ComparisonMatrix";
import { ComparisonSummary } from "../src/components/RecordSections";
import type { Comparison, MetricComparison, Run } from "../src/api/types";
import storedB1 from "./fixtures/run_compare_b1.json";
import storedLegacy from "./fixtures/run_compare_auditor.json";

function row(over: Partial<MetricComparison>): MetricComparison {
  return {
    metric: "revenue", label: "Revenue", family: "currency", status: "MATCH",
    value_a: 109417000000, value_b: 109400000000, raw_a: "109,417,000,000", raw_b: "$109.4 billion",
    period_a: "Q3 FY2026", period_b: "Q3 FY2026", variance_pct: 0.02, note: null, flags: false, ...over,
  };
}

const ROWS: MetricComparison[] = [
  row({ metric: "eps_diluted", label: "Diluted EPS", family: "per_share", status: "MISMATCH",
        value_a: 2.02, value_b: 2.1, raw_a: "$2.02", raw_b: "$2.10", variance_pct: 3.81, flags: true }),
  row({ metric: "asset_turnover", label: "Asset turnover", family: "derived", status: "DERIVED_WRONG",
        value_a: 0.13, value_b: null, raw_a: "0.13", raw_b: null, period_b: null, variance_pct: null,
        note: "Recomputed from agent A's own figures and inputs: Revenue / Total assets = 0.693." }),
  row({}),
  row({ metric: "total_assets", label: "Total assets", status: "ONE_SIDED", value_b: null, raw_b: null,
        period_b: null, variance_pct: null }),
];

describe("ComparisonMatrix", () => {
  it("puts the difference and its verdict between the two agents' values", () => {
    render(<ComparisonMatrix rows={ROWS} />);
    const eps = screen.getByRole("rowheader", { name: "Diluted EPS" }).closest("tr")!;
    const cells = within(eps).getAllByRole("cell");
    expect(cells.map((c) => c.getAttribute("data-label"))).toEqual(["Period", "Agent A", "Difference", "Agent B"]);
    expect(cells[2]).toHaveTextContent("Mismatch 3.81%");
    expect(within(cells[2]).getByText(/Mismatch/).closest(".badge")).toHaveClass("badge-danger");
  });

  it("formats values for reading and keeps each agent's own wording in the tooltip", () => {
    render(<ComparisonMatrix rows={ROWS} />);
    // Two synthetic rows share this raw value; the first is the Revenue row.
    const figure = screen.getAllByTitle("As agent A wrote it: 109,417,000,000")[0];
    expect(figure).toHaveTextContent("$109.42B");
  });

  it("explains a derived figure that doesn't add up, in the row itself", () => {
    render(<ComparisonMatrix rows={ROWS} />);
    expect(screen.getByText("Doesn't add up")).toBeInTheDocument();
    expect(screen.getByText(/Revenue \/ Total assets = 0.693/)).toBeInTheDocument();
  });

  it("folds away figures only one agent was given, so they never read as a disagreement", () => {
    const { container } = render(<ComparisonMatrix rows={ROWS} />);
    const group = container.querySelector("details.mx-one-sided")!;
    expect(group).not.toHaveAttribute("open");
    expect(group).toHaveTextContent("Figures only one agent was given (1)");
  });

  it("filters to conflicts", () => {
    render(<ComparisonMatrix rows={ROWS} />);
    fireEvent.click(screen.getByRole("button", { name: /Conflicts/ }));
    const shown = screen.getAllByRole("rowheader").map((h) => h.textContent);
    expect(shown).toEqual(["Diluted EPS", "Asset turnover", "Total assets"]); // last one: the folded group
  });

  it("says so when there is nothing to compare", () => {
    render(<ComparisonMatrix rows={[]} />);
    expect(screen.getByText(/nothing to compare/)).toBeInTheDocument();
  });
});

describe("ComparisonSummary with figure rows", () => {
  it("real stored run: headline from the rows, verdict and its rule shown", () => {
    const run = storedB1 as unknown as Run;
    render(<ComparisonSummary cmp={run.cross_agent_comparison!} producers={run.producers} facts={run.facts} />);
    // MSFT, stored: nothing shared, three search-sourced figures on one side — the
    // headline names that reason rather than only saying nothing conflicts.
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(
      "No conflicting figures, but 3 figures cited by only one agent, with nothing to back them up");
    expect(screen.getByText(/Recorded verdict \(figure-by-figure rule\)/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Figure by figure" })).toBeInTheDocument();
  });

  it("when the recorded verdict and the figure check disagree, both are shown and the gap is named", () => {
    const cmp = { ...(storedB1 as unknown as Run).cross_agent_comparison!, contradiction_rule: "concept_aware",
                  contradiction_flag: true, metric_comparisons: [row({})] } as Comparison;
    render(<ComparisonSummary cmp={cmp} />);
    expect(screen.getByText("Flagged for review")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/concept-aware rule.*finds nothing that conflicts/);
  });

  it("disagreement headline counts only real mismatches", () => {
    const cmp = { ...(storedB1 as unknown as Run).cross_agent_comparison!, metric_comparisons: ROWS } as Comparison;
    render(<ComparisonSummary cmp={cmp} />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("The agents disagree on 1 of 2 shared figures");
    expect(screen.getByText(/1 figure that doesn't add up/)).toBeInTheDocument();
  });

  it("a shared figure that couldn't be compared is neither agreement nor conflict", () => {
    const cmp = { ...(storedB1 as unknown as Run).cross_agent_comparison!,
      metric_comparisons: [row({ status: "UNVERIFIABLE_PERIOD", period_a: null, variance_pct: null })] } as Comparison;
    render(<ComparisonSummary cmp={cmp} />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("No conflicting figures among 1 shared figure");
    expect(screen.getByText(/1 shared figure couldn't be compared/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2 })).not.toHaveTextContent(/0 of 1/);
  });

  it("runs stored before B1 fall back to the old comparison, saying so", () => {
    const run = storedLegacy as unknown as Run;
    render(<ComparisonSummary cmp={run.cross_agent_comparison!} />);
    expect(screen.getByText(/stored before figure-by-figure comparison/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Figure by figure" })).toBeNull();
  });
});
