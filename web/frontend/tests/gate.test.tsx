import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RunDetailView } from "../src/views/RunDetail";
import { HistoryPanel } from "../src/views/HistoryPanel";
import { DecisionGate } from "../src/components/DecisionGate";
import { _resetTokens } from "../src/api/client";
import type { Gate, Run } from "../src/api/types";
import gatedAuditor from "./fixtures/run_compare_gated_auditor.json";
import gatedInvestor from "./fixtures/run_compare_gated_investor.json";

// Both fixtures are the same real run (live, 2026-09-25: llama3.2 said the first
// Pokémon game reached North America in 1998, qwen2.5 said 1996), read back through
// GET /api/runs/{id} at each scope — so what the investor view withholds is what the
// server actually withheld, not a hand-made imitation of it.

const auditorRun = gatedAuditor as unknown as Run;
const investorRun = gatedInvestor as unknown as Run;
const rows = auditorRun.cross_agent_comparison!.metric_comparisons!;

beforeEach(() => { sessionStorage.clear(); _resetTokens(); });

function fill() {
  fireEvent.click(screen.getByRole("button", { name: "Review and record decision" }));
  fireEvent.click(screen.getByRole("radio", { name: /Agent A's figure is right/ }));
  fireEvent.change(screen.getByLabelText(/Why\? What did you check\?/),
                   { target: { value: "Nintendo's own history gives September 1998 for North America." } });
  fireEvent.change(screen.getByLabelText(/^Your name/), { target: { value: "Divij" } });
}

describe("DecisionGate — awaiting a decision", () => {
  beforeEach(() => { sessionStorage.clear(); _resetTokens(); });
  afterEach(() => vi.restoreAllMocks());

  it("sits under the verdict, names the disputed figure, and expands inline (no dialog)", () => {
    render(<RunDetailView run={auditorRun} flags={[]} scope="auditor" />);
    expect(screen.getAllByText("Needs decision").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: /disagree on a figure: a reviewer must decide/ })).toBeInTheDocument();
    const cta = screen.getByRole("button", { name: "Review and record decision" });
    expect(cta).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(cta);
    expect(cta).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByRole("dialog")).toBeNull();
    // The matrix stays on screen beside the form.
    expect(screen.getByRole("table", { name: /Figures both agents cited/ })).toBeInTheDocument();
    // A year gap reads in years, not as the (true but useless) 0.1% variance.
    expect(screen.getByText("Mismatch 2 years")).toBeInTheDocument();
    const item = screen.getByRole("checkbox", { name: /Release year/ });
    expect(item).toBeChecked();
    expect(within(item.closest("label")!).getByText("A 1998")).toBeInTheDocument();
  });

  it("won't submit until every rule the server enforces is met", () => {
    render(<DecisionGate runId={auditorRun.run_id} gate={auditorRun.gate!} rows={rows} scope="auditor" />);
    fireEvent.click(screen.getByRole("button", { name: "Review and record decision" }));
    const submit = screen.getByRole("button", { name: "Record decision" });
    expect(submit).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /Agent A's figure is right/ }));
    fireEvent.change(screen.getByLabelText(/Why\?/), { target: { value: "too short" } });
    fireEvent.change(screen.getByLabelText(/^Your name/), { target: { value: "Divij" } });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/9 \/ 20 characters minimum/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Why\?/), { target: { value: "Nintendo's history gives 1998." } });
    expect(submit).toBeEnabled();
    // A set value needs a number.
    fireEvent.click(screen.getByRole("radio", { name: /Set the correct value/ }));
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Correct value/), { target: { value: "1998" } });
    expect(submit).toBeEnabled();
  });

  it("posts exactly what the reviewer chose, with the auditor token, and reports the new gate", async () => {
    const decided: Gate = { ...auditorRun.gate!, status: "DECIDED", pending: [] };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/auth/token") return new Response(JSON.stringify({ access_token: "tok-a" }));
      expect(url).toBe(`/api/runs/${auditorRun.run_id}/decisions`);
      expect((init!.headers as Record<string, string>).Authorization).toBe("Bearer tok-a");
      expect(JSON.parse(init!.body as string)).toEqual({
        decision: "accept_a", decided_by: "Divij", cited_items: ["release_year"], final_value: null, final_grade: null,
        rationale: "Nintendo's own history gives September 1998 for North America.",
      });
      return new Response(JSON.stringify(decided));
    });
    vi.stubGlobal("fetch", fetchMock);
    const onDecided = vi.fn();
    render(<DecisionGate runId={auditorRun.run_id} gate={auditorRun.gate!} rows={rows} scope="auditor" onDecided={onDecided} />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await waitFor(() => expect(onDecided).toHaveBeenCalledWith(decided));
    expect(sessionStorage.getItem(`gate-draft:${auditorRun.run_id}`)).toBeNull();
  });

  it("shows the server's reason when a decision is refused", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url === "/api/auth/token"
      ? new Response(JSON.stringify({ access_token: "t" }))
      : new Response(JSON.stringify({ detail: "Not a mismatched figure in this run: x." }), { status: 422 })));
    render(<DecisionGate runId={auditorRun.run_id} gate={auditorRun.gate!} rows={rows} scope="auditor" />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 422): Not a mismatched figure in this run: x.");
  });

  it("keeps a half-written decision if the reviewer navigates away and back", () => {
    const { unmount } = render(<DecisionGate runId={auditorRun.run_id} gate={auditorRun.gate!} rows={rows} scope="auditor" />);
    fill();
    unmount();
    render(<DecisionGate runId={auditorRun.run_id} gate={auditorRun.gate!} rows={rows} scope="auditor" />);
    expect(screen.getByRole("button", { name: /Review and record decision \(draft saved\)/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Review and record decision/ }));
    expect(screen.getByLabelText(/^Your name/)).toHaveValue("Divij");
  });
});

describe("Investor scope while a decision is pending", () => {
  it("announces what is withheld instead of showing empty boxes, and offers no form", () => {
    render(<RunDetailView run={investorRun} flags={[]} scope="investor" />);
    expect(screen.getAllByText("Conclusion withheld: pending human review.")).toHaveLength(2);
    const cells = screen.getAllByText("Withheld");
    expect(cells).toHaveLength(2); // A and B values of the disputed figure
    expect(screen.getByText("Pending human review. An auditor records the decision.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /record decision/i })).toBeNull();
    expect(document.body.textContent).not.toMatch(/199[68]/);
  });
});

describe("DecisionGate — decided", () => {
  it("collapses to the decision record, with superseded decisions kept below", () => {
    const d = (id: string, decision: "accept_a" | "accept_b", superseded: boolean) => ({
      decision_id: id, run_id: auditorRun.run_id, decided_by: "Divij", decision, final_value: null,
      rationale: `Reason for ${id}, long enough.`, cited_items: ["release_year"], decided_at: "2026-09-25T12:00:00Z", superseded,
    });
    const gate: Gate = {
      ...auditorRun.gate!, status: "DECIDED", pending: [],
      items: auditorRun.gate!.items.map((i) => ({ ...i, decision_id: "d2" })),
      decisions: [d("d2", "accept_a", false), d("d1", "accept_b", true)],
    };
    render(<DecisionGate runId={auditorRun.run_id} gate={gate} rows={rows} scope="auditor" />);
    expect(screen.getByRole("heading", { name: "A reviewer has decided every disputed figure" })).toBeInTheDocument();
    const inEffect = screen.getByRole("list", { name: "Decisions in effect" });
    expect(within(inEffect).getByText("Agent A's figure is right")).toBeInTheDocument();
    expect(screen.getByText(/Earlier decisions, since superseded \(1\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Record a new decision" })).toBeInTheDocument();
  });
});

describe("History — needs decision", () => {
  afterEach(() => vi.restoreAllMocks());

  it("marks gated runs and lists them under their own tab, reporting the count", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
      url === "/api/runs" ? [auditorRun] : []))));
    const onCount = vi.fn();
    render(<HistoryPanel selectedId={null} onSelect={() => {}} onNeedsDecision={onCount} />);
    await waitFor(() => expect(onCount).toHaveBeenCalledWith(1));
    expect(screen.getByText("Mismatch · 1 of 1 figures")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Decide/ }));
    expect(within(screen.getByRole("tabpanel")).getByText(auditorRun.subject!)).toBeInTheDocument();
  });
});
