import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { RunDetailView } from "../src/views/RunDetail";
import { AssessmentView, CompareModeView, RawOutput, asksForAssessment, summaryLines } from "../src/components/Assessment";
import { CompareForm } from "../src/views/CompareView";
import type { ReasoningObject, Run } from "../src/api/types";
import auditorFixture from "./fixtures/run_compare_b4_auditor.json";
import investorFixture from "./fixtures/run_compare_b4_investor.json";

// Both fixtures are the same real run (live, 2026-09-26, directive v1.6.0, AAPL, the
// original pairing): the filings agent answered with a partial assessment (grade A,
// hold; its key_metrics used XBRL tag names, which the closed vocabulary refused), and
// the earnings agent failed the format check twice. Read back at each scope.

const auditor = auditorFixture as unknown as Run;
const investor = investorFixture as unknown as Run;

function ro(over: Partial<ReasoningObject>): ReasoningObject {
  return {
    reasoning_id: "r", run_id: "x", agent_id: "bull", attempt_number: 1, parse_status: "SUCCESS",
    confidence_score: 0.7, directive_version: "v1.6.0", raw_output: { text: "" }, ...over,
  };
}

beforeEach(() => vi.restoreAllMocks());

describe("U7 — the assessment strip, from the real record", () => {
  it("shows grade, direction and assumptions in the agent's lane, labelled as a model judgment", () => {
    render(<RunDetailView run={auditor} flags={[]} scope="auditor" />);
    const lane = screen.getByRole("article", { name: "Agent A" });
    const strip = lane.querySelector(".assessment") as HTMLElement;
    expect(within(strip).getByText("Grade")).toBeInTheDocument();
    expect(within(strip).getByText("A")).toHaveClass("grade-badge");
    expect(within(strip).getByText("Hold")).toBeInTheDocument();
    expect(within(strip).getByText("Model judgment")).toBeInTheDocument();
    expect(within(strip).getByRole("rowheader", { name: "Horizon" }).closest("tr")).toHaveTextContent("12 months");
    // Partial: what was refused is listed, folded.
    const issues = within(strip).getByText(/Some of the block was left out \(1\)/).closest("details")!;
    expect(issues.open).toBe(false);
    expect(within(issues).getByText(/key_metrics not recognised: 'Assets'/)).toBeInTheDocument();
  });

  it("is withheld at investor scope, and says so", () => {
    render(<RunDetailView run={investor} flags={[]} scope="investor" />);
    const lane = screen.getByRole("article", { name: "Agent A" });
    expect(lane.querySelector(".assessment")).toBeNull();
    expect(within(lane).getByText("The structured assessment is withheld at investor scope.")).toBeInTheDocument();
  });
});

describe("U7 — when there is no usable assessment", () => {
  it("a broken block says why, and opens the raw output in place with the block marked", () => {
    const raw = '<thought_log>\n x\n</thought_log>\n<conclusion>\n y\n</conclusion>\n<assessment>{"grade": BBB}</assessment>';
    render(<AssessmentView ro={ro({ assessment_status: "invalid_json", assessment: null, raw_output: { text: raw },
      assessment_issues: ["JSON didn't parse at line 1, column 11: Expecting value."] })} />);
    expect(screen.getByText(/didn't give a usable one/)).toHaveTextContent("JSON didn't parse at line 1");
    const button = screen.getByRole("button", { name: "View raw output" });
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(document.querySelector("mark.raw-region")!.textContent).toBe('assessment block: <assessment>{"grade": BBB}</assessment>');
  });

  it("an unclosed block is marked to the end of the output", () => {
    const { container } = render(<RawOutput raw={'<conclusion>\n y\n</conclusion>\n<assessment>{"grade": "BBB"'} />);
    expect(container.querySelector("mark.raw-region")!.textContent).toContain('{"grade": "BBB"');
  });

  it("a block that was simply left out is allowed behaviour, not an error", () => {
    render(<AssessmentView ro={ro({ assessment_status: "absent", assessment: null })} />);
    expect(screen.getByText(/left the optional assessment out/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "View raw output" })).toBeNull();
  });

  it("where none was asked for, nothing is shown", () => {
    const { container: retry } = render(<AssessmentView ro={ro({ assessment_status: null, directive_version: "corrective" })} />);
    expect(retry.textContent).toBe("");
    const { container: old } = render(<AssessmentView ro={ro({ directive_version: "v1.5.2" })} />);
    expect(old.textContent).toBe("");
  });

  it("knows which directives ask for one", () => {
    expect(asksForAssessment("v1.6.0")).toBe(true);
    expect(asksForAssessment("v1.10.0")).toBe(true); // not a string comparison
    expect(asksForAssessment("v1.5.2")).toBe(false);
    expect(asksForAssessment("corrective")).toBe(false);
  });
});

describe("U7 — mobile Compare mode", () => {
  it("shows each side's grade, direction and key points, with the disputed figures first", () => {
    render(<CompareModeView
      rows={[{ metric: "revenue", label: "Revenue", family: "currency", status: "MISMATCH", value_a: 1, value_b: 2,
        raw_a: "$1", raw_b: "$2", period_a: null, period_b: null, variance_pct: 50, note: null, flags: true }]}
      lanes={[
        { slot: "a", label: "Bull analyst", conclusion: null, withheld: false,
          ro: ro({ assessment: { grade: "A", direction: "buy", key_points: ["Margins held."] }, assessment_status: "valid" }) },
        { slot: "b", label: "Bear analyst", withheld: false, ro: ro({ agent_id: "bear", thought_log: "- Debt rose.\n- Cash fell.\nnot a bullet" }),
          conclusion: "Revenue fell. Costs rose. Outlook weak." },
      ]} />);
    expect(screen.getByText(/Disagreements:/).parentElement).toHaveTextContent("Revenue");
    const bull = screen.getByRole("region", { name: "Bull analyst" });
    expect(within(bull).getByText("buy")).toBeInTheDocument();
    expect(within(bull).getByText("Margins held.")).toBeInTheDocument();
    expect(within(bull).queryByText("Excerpt")).toBeNull();
    const bear = screen.getByRole("region", { name: "Bear analyst" });
    expect(within(bear).getByText("No structured grade")).toBeInTheDocument();
    expect(within(bear).getByText("Excerpt")).toBeInTheDocument(); // never presented as the agent's own summary
    expect(within(bear).getByText("Debt rose.")).toBeInTheDocument();
  });

  it("falls back to the conclusion's first two sentences, and never an invented summary", () => {
    expect(summaryLines(undefined, "One. Two! Three?")).toEqual({ lines: ["One.", "Two!"], excerpt: true });
  });

  it("is offered as a choice alongside one-lane-at-a-time tabs", () => {
    render(<RunDetailView run={auditor} flags={[]} scope="auditor" />);
    const group = screen.getByRole("group", { name: "Show" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Agent A", "Agent B", "Compare mode"]);
    fireEvent.click(within(group).getByRole("button", { name: "Compare mode" }));
    expect(screen.getByLabelText("Compare mode")).toBeInTheDocument();
  });
});

describe("B4 — choosing the bull/bear pairing", () => {
  it("sends the pairing only when bull vs bear is chosen", () => {
    const onStart = vi.fn();
    render(<CompareForm onStart={onStart} disabled={false} />);
    fireEvent.change(screen.getByLabelText("Ticker"), { target: { value: "nvda" } });
    fireEvent.click(screen.getByRole("button", { name: "Run both agents" }));
    expect(onStart).toHaveBeenLastCalledWith({ ticker: "NVDA" }, "NVDA");
    fireEvent.click(screen.getByRole("radio", { name: /Bull vs bear/ }));
    fireEvent.click(screen.getByRole("button", { name: "Run both agents" }));
    expect(onStart).toHaveBeenLastCalledWith({ ticker: "NVDA", pairing: "bull_bear" }, "NVDA");
  });

  it("labels the lanes Bull and Bear", () => {
    const run = { ...auditor, producers: { ...auditor.producers!,
      a: { ...auditor.producers!.a, role: "BULL" }, b: { ...auditor.producers!.b, role: "BEAR" } } } as Run;
    render(<RunDetailView run={run} flags={[]} scope="auditor" />);
    expect(within(screen.getByRole("article", { name: "Agent A" })).getByText("Bull analyst")).toBeInTheDocument();
    expect(within(screen.getByRole("article", { name: "Agent B" })).getByText("Bear analyst")).toBeInTheDocument();
  });
});
