import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RunDetailView } from "../src/views/RunDetail";
import { HistoryPanel } from "../src/views/HistoryPanel";
import type { Run } from "../src/api/types";
import chatAuditor from "./fixtures/run_chat_auditor.json";
import chatInvestor from "./fixtures/run_chat_investor.json";
import compareStored from "./fixtures/run_compare_auditor.json";
import storedB1 from "./fixtures/run_compare_b1.json";

// Fixtures are real stored runs exported from web/data (GET /api/runs/{id} shape).

describe("RunDetailView — parity with the legacy run modal", () => {
  it("chat run: the user's subject, the agent's conclusion, and the verifier's source check", () => {
    const run = chatAuditor as unknown as Run;
    render(<RunDetailView run={run} flags={[]} scope="auditor" />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(run.subject!);
    expect(screen.getByRole("heading", { name: "Conclusion" })).toBeInTheDocument();
    expect(screen.getByText(/source verification|not verified/i)).toBeInTheDocument();
    expect(screen.getByText("Reasoning")).toBeInTheDocument();
    expect(screen.getByText(/What the agent was sent/)).toBeInTheDocument();
    expect(screen.getByText("Run trace")).toBeInTheDocument();
  });

  it("investor-scope run: withheld material is announced, never shown", () => {
    const run = chatInvestor as unknown as Run;
    expect(run.reasoning_objects.some((o) => "thought_log" in o)).toBe(false); // the fixture really is redacted
    render(<RunDetailView run={run} flags={[]} scope="investor" />);
    expect(screen.getByText(/Reasoning is withheld at investor scope/)).toBeInTheDocument();
    expect(screen.getByText(/What the agent was sent is withheld at investor scope/)).toBeInTheDocument();
    expect(screen.queryByText("Reasoning", { selector: ".section-title" })).toBeNull();
  });

  it("stored compare run (7 keys only) still renders both agents and the comparison", () => {
    const run = compareStored as unknown as Run;
    render(<RunDetailView run={run} flags={[]} scope="auditor" />);
    expect(screen.getByLabelText("Agent A")).toBeInTheDocument();
    expect(screen.getByLabelText("Agent B")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 2 })[0]).toHaveTextContent(/figures/i);
    // Both agents' prompts, labeled as such — not "2 attempts" of one agent.
    expect(screen.getByText(/What each agent was sent \(2 prompts, 2 agents\)/)).toBeInTheDocument();
  });

  it("reviewer flag form is offered at auditor scope only", () => {
    const run = chatAuditor as unknown as Run;
    const { rerender } = render(<RunDetailView run={run} flags={[]} scope="auditor" />);
    expect(screen.getByRole("button", { name: "Add flag" })).toBeInTheDocument();
    rerender(<RunDetailView run={run} flags={[]} scope="investor" />);
    expect(screen.queryByRole("button", { name: "Add flag" })).toBeNull();
  });
});

describe("HistoryPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  function mockApi() {
    const runs = [chatAuditor, compareStored];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const body = url === "/api/runs" ? runs
        : url === "/api/sessions" ? [{ run_id: "s1", ticker: "AAPL", status: "COMPLETE", directive_version: "v1.5.1", initiated_at: "" }]
        : url === "/api/runs/contradictions" ? []
        : [];
      return new Response(JSON.stringify(body), { status: 200 });
    }));
  }

  it("lists runs and reports the selection", async () => {
    mockApi();
    const onSelect = vi.fn();
    render(<HistoryPanel selectedId={null} onSelect={onSelect} />);
    const subject = (chatAuditor as unknown as Run).subject!;
    const card = await screen.findByText(subject);
    fireEvent.click(card);
    expect(onSelect).toHaveBeenCalledWith((chatAuditor as unknown as Run).run_id);
  });

  it("is a real tablist: arrow keys move selection and focus", async () => {
    mockApi();
    render(<HistoryPanel selectedId={null} onSelect={() => {}} />);
    const runsTab = screen.getByRole("tab", { name: /Runs/ });
    expect(runsTab).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(runsTab, { key: "ArrowRight" });
    const sessionsTab = screen.getByRole("tab", { name: /Sessions/ });
    expect(sessionsTab).toHaveAttribute("aria-selected", "true");
    expect(sessionsTab).toHaveFocus();
    await waitFor(() => expect(screen.getByText(/AAPL · directive v1.5.1/)).toBeInTheDocument());
    fireEvent.keyDown(sessionsTab, { key: "ArrowLeft" });
    expect(runsTab).toHaveAttribute("aria-selected", "true");
  });

  it("run cards give the same reading as the run's headline", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
      url === "/api/runs" ? [storedB1] : []), { status: 200 })));
    render(<HistoryPanel selectedId={null} onSelect={() => {}} />);
    // Stored MSFT B1 run: flagged for one-sided figures, nothing actually mismatched.
    expect(await screen.findByText(/Needs review \(3\)/)).toBeInTheDocument();
    expect(screen.queryByText(/^Mismatch ·/)).toBeNull(); // nothing actually mismatched
  });

  it("shows an empty Flagged tab in plain words", async () => {
    mockApi();
    render(<HistoryPanel selectedId={null} onSelect={() => {}} />);
    fireEvent.click(screen.getByRole("tab", { name: /Flagged/ }));
    expect(await screen.findByText(/No compare runs with differing figures/)).toBeInTheDocument();
  });
});
