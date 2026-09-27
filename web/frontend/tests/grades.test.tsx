import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RunDetailView } from "../src/views/RunDetail";
import { DecisionGate } from "../src/components/DecisionGate";
import { GradesPanel } from "../src/components/Grades";
import { _resetTokens } from "../src/api/client";
import type { Gate, Run, Synthesis } from "../src/api/types";
import auditorFixture from "./fixtures/run_compare_b5_auditor.json";
import investorFixture from "./fixtures/run_compare_b5_investor.json";

// Real run (live, 2026-09-26, AAPL, extraction assess-extract-v1): both agents passed
// the format check; the extraction read AAA/buy and A/buy; B5 classed the difference as
// an assumption (stable vs expanding margins) and the gate asks a human for the grade.
// Read back at each scope.

const auditor = auditorFixture as unknown as Run;
const investor = investorFixture as unknown as Run;

beforeEach(() => { sessionStorage.clear(); _resetTokens(); vi.restoreAllMocks(); });

describe("U8 — grades in the review summary", () => {
  it("names the kind of disagreement, then each grade beside its counted evidence", () => {
    render(<RunDetailView run={auditor} flags={[]} scope="auditor" />);
    const panel = screen.getByLabelText("Grade");
    expect(within(panel).getByText("They disagree because of an assumption.")).toBeInTheDocument();
    expect(panel).toHaveTextContent("agent A assumes stable margins, agent B assumes expanding margins");
    const a = within(panel).getByRole("region", { name: "Agent A's grade" });
    expect(within(a).getByText("AAA")).toBeInTheDocument();
    expect(within(a).getByLabelText("Evidence behind this grade")).toHaveTextContent("2 figures match the filing");
    expect(within(panel).getByText(/model judgment/)).toBeInTheDocument();
    expect(within(panel).getByText("No consensus: needs your decision")).toBeInTheDocument();
    // Counts, not a score: no gauge, no percentage.
    expect(panel.textContent).not.toMatch(/\d+%\s*(confidence|score)/i);
  });

  it("at investor scope: no agent grade anywhere, only the kind of disagreement", () => {
    render(<RunDetailView run={investor} flags={[]} scope="investor" />);
    expect(screen.getByText(/stay internal until a reviewer sets the grade/)).toBeInTheDocument();
    expect(screen.getByText("They disagree because of an assumption.")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("AAA");
  });

  it("shows a grade a reviewer set, to investors too", () => {
    const decided = { grade: "AA", decided_by: "Divij", decided_at: "2026-09-26T12:00:00Z", decision_id: "d1" };
    render(<GradesPanel synthesis={investor.cross_agent_comparison!.synthesis as Synthesis} decided={decided} />);
    expect(screen.getByText("Grade set by a reviewer")).toBeInTheDocument();
    expect(screen.getByText("AA")).toBeInTheDocument();
    expect(screen.getByText(/by Divij/)).toBeInTheDocument();
  });

  it("a consensus is shown as proposed, never as published", () => {
    const syn = { ...(auditor.cross_agent_comparison!.synthesis as Synthesis), primary_conflict_driver: "none" as const,
      consensus_grade: { grade: "A", direction: "buy" }, needs_decision: false };
    render(<GradesPanel synthesis={syn} />);
    expect(screen.getByText("Consensus")).toBeInTheDocument();
    expect(screen.getByText(/Investors see no grade until a reviewer sets one/)).toBeInTheDocument();
  });
});

describe("U8 — setting the grade through the gate", () => {
  const gate = auditor.gate as Gate;

  it("offers the grade decisions, with both candidates and the closed grade list", () => {
    render(<DecisionGate runId={auditor.run_id} gate={gate} rows={[]} scope="auditor" />);
    expect(screen.getByRole("heading", { name: "The agents' grades differ: a reviewer must set the grade" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review and record decision" }));
    const item = screen.getByRole("checkbox", { name: /set the grade/ }).closest("label")!;
    expect(item).toHaveTextContent("A AAA · buy");
    expect(item).toHaveTextContent("B A · buy");
    expect(screen.getAllByRole("radio").map((r) => (r as HTMLInputElement).value)).toEqual(["accept_a", "accept_b", "set_grade"]);
    expect(screen.getByRole("radio", { name: /Agent A's grade is right/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /Set the grade/ }));
    expect([...(screen.getByLabelText("Grade") as HTMLSelectElement).options].map((o) => o.value))
      .toEqual(["", "AAA", "AA", "A", "BBB", "BB", "B", "CCC"]);
  });

  it("won't submit Set the grade without a grade, and sends it when chosen", async () => {
    const posted: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/auth/token") return new Response(JSON.stringify({ access_token: "t" }));
      posted.push(JSON.parse(init!.body as string));
      return new Response(JSON.stringify({ ...gate, status: "DECIDED", pending: [] }));
    }));
    const onDecided = vi.fn();
    render(<DecisionGate runId={auditor.run_id} gate={gate} rows={[]} scope="auditor" onDecided={onDecided} />);
    fireEvent.click(screen.getByRole("button", { name: "Review and record decision" }));
    fireEvent.click(screen.getByRole("radio", { name: /Set the grade/ }));
    fireEvent.change(screen.getByLabelText(/Why\?/), { target: { value: "Both see strong figures; margins are the open question." } });
    fireEvent.change(screen.getByLabelText(/^Your name/), { target: { value: "Divij" } });
    const submit = screen.getByRole("button", { name: "Record decision" });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/Choose the grade/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Grade"), { target: { value: "AA" } });
    fireEvent.click(submit);
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    expect(posted[0]).toMatchObject({ decision: "set_grade", final_grade: "AA", cited_items: ["grade"], final_value: null });
  });
});
