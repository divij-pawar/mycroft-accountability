import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ComparisonSummary } from "../src/components/RecordSections";
import { DecisionGate } from "../src/components/DecisionGate";
import { SourcesProvider, sourcesOf } from "../src/components/SourcePopover";
import type { Check, Gate, Run } from "../src/api/types";
import storedAapl from "./fixtures/run_compare_b3_aapl.json";

// run_compare_b3_aapl.json is a real stored run (live, 2026-09-25, lens v2): both agents
// cited the shared net income and EPS, and every accounting check passed.

const aapl = storedAapl as unknown as Run;

beforeEach(() => sessionStorage.clear());

describe("B2 — shared figures", () => {
  it("the two agents' shared figures meet in the matrix", () => {
    render(<ComparisonSummary cmp={aapl.cross_agent_comparison!} producers={aapl.producers} facts={aapl.facts} />);
    const row = screen.getByRole("rowheader", { name: "Net income" }).closest("tr")!;
    expect(within(row).getByText("Match")).toBeInTheDocument();
    expect(aapl.producers!.shared_concepts).toEqual(["EarningsPerShareDiluted", "NetIncomeLoss"]);
  });
});

const FAILING: Check[] = [
  { key: "source:agent_a:revenue", rule: "matches_source", kind: "hard", scope: "agent_a", who: "Agent A",
    outcome: "fail", plain: "Revenue matches the filing value the agent was given",
    math: "cited $94.9 billion; given $109.4B (3 months ending 2026-06-27, 10-Q)", reason: null,
    metrics: ["revenue"], gates: true },
  { key: "check:source:net_le_operating", rule: "net_le_operating", kind: "heuristic", scope: "source",
    who: "The filing", outcome: "fail", plain: "Net income is usually no more than operating income",
    math: "net income $112.2B vs operating income $40.77B", reason: "Not an identity: a one-off gain…",
    metrics: ["net_income", "operating_income"], gates: false },
];

describe("U5 — review summary and constraint checklist", () => {
  it("answer first: headline, per-agent evidence, and the issues count come before the figures", () => {
    render(<ComparisonSummary cmp={aapl.cross_agent_comparison!} producers={aapl.producers} facts={aapl.facts} />);
    const summary = screen.getByLabelText("Evidence and accounting checks");
    expect(within(summary).getByText(/Agent A: 2 of 2 figures it cited from its inputs match the SEC filing/)).toBeInTheDocument();
    expect(within(summary).getByText(/Agent B: 1 of 1 figures/)).toBeInTheDocument();
    expect(within(summary).getByText("No accounting issues found")).toBeInTheDocument();
    // The summary precedes the matrix in reading order.
    const table = screen.getByRole("table", { name: /Figures both agents cited/ });
    expect(summary.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("folds what passed, counted, with the math one click away", () => {
    render(<ComparisonSummary cmp={aapl.cross_agent_comparison!} producers={aapl.producers} facts={aapl.facts} />);
    const passed = screen.getByText("8 checks passed").closest("details")!;
    expect(passed.open).toBe(false);
    expect(within(passed).getAllByText("Matches the filing")).toHaveLength(3);
    expect(within(passed).getByText(/assets \$383\.3B vs liabilities and equity \$383\.3B/)).toBeInTheDocument();
  });

  it("a failed hard check is open, says it is held for a decision, and is counted as such", () => {
    const cmp = { ...aapl.cross_agent_comparison!, structural_flags: { policy: "b3-v1", checks: FAILING, gating: [FAILING[0].key] } };
    render(<ComparisonSummary cmp={cmp} producers={aapl.producers} facts={aapl.facts} />);
    expect(screen.getByText("1 accounting issue: 1 needs a decision")).toBeInTheDocument();
    expect(screen.getByText("1 unusual figure, worth a look")).toBeInTheDocument();
    expect(screen.getByText(/Agent A: 0 of 1 figures it cited from its inputs match/)).toBeInTheDocument();
    const hard = document.querySelector('[data-key="source:agent_a:revenue"] details') as HTMLDetailsElement;
    expect(hard.open).toBe(true);
    expect(within(hard).getByText("Held for review: a reviewer must decide.")).toBeInTheDocument();
    expect(within(hard).getByText(/cited \$94\.9 billion; given \$109\.4B/)).toBeInTheDocument();
    const warn = document.querySelector('[data-key="check:source:net_le_operating"] details') as HTMLDetailsElement;
    expect(within(warn).getByText("Unusual, worth a look. Not an error on its own.")).toBeInTheDocument();
  });

  it("marks, in the matrix cell, whether the agent's figure matches the filing", () => {
    render(<SourcesProvider value={sourcesOf(aapl)}>
      <ComparisonSummary cmp={aapl.cross_agent_comparison!} producers={aapl.producers} facts={aapl.facts} />
    </SourcesProvider>);
    const row = screen.getByRole("rowheader", { name: "Net income" }).closest("tr")!;
    expect(within(row).getAllByText("Matches the filing")).toHaveLength(2); // A's and B's
  });
});

describe("DecisionGate — failed checks", () => {
  const gate: Gate = {
    status: "AWAITING_DECISION", policy: "v2", identity_note: "not authenticated",
    items: [
      { metric: "release_year", kind: "figure", label: "Release year", status: "MISMATCH", decision_id: null, metrics: ["release_year"] },
      { metric: "source:agent_a:revenue", kind: "check", label: "Agent A: Revenue matches the filing value the agent was given",
        status: "CHECK_FAILED", decision_id: null, metrics: ["revenue"], scope: "agent_a",
        math: "cited $94.9 billion; given $109.4B" },
    ],
    pending: ["release_year", "source:agent_a:revenue"],
    decisions: [],
  };

  it("names both kinds of problem, shows the check's arithmetic, and offers only decisions that fit", () => {
    render(<DecisionGate runId="r" gate={gate} rows={[]} scope="auditor" />);
    expect(screen.getByRole("heading", { name: /1 disputed figure and 1 failed check/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review and record decision" }));
    expect(screen.getByText("cited $94.9 billion; given $109.4B")).toBeInTheDocument();
    const radios = () => screen.getAllByRole("radio").map((r) => (r as HTMLInputElement).value);
    // Both items picked: only decisions valid for a figure AND a check.
    expect(radios()).toEqual(["not_a_conflict", "override_value"]);
    fireEvent.click(screen.getByRole("checkbox", { name: /Release year/ }));
    expect(radios()).toEqual(["not_a_conflict", "override_value", "confirmed_error"]);
    fireEvent.click(screen.getByRole("checkbox", { name: /Release year/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Revenue matches/ }));
    expect(radios()).toContain("accept_a");
    expect(radios()).not.toContain("confirmed_error");
  });
});
