import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RunDetailView } from "../src/views/RunDetail";
import { FilingSource, SourcesProvider, WebSource, figureSpans, sourcesOf } from "../src/components/SourcePopover";
import { _resetTokens } from "../src/api/client";
import type { Run } from "../src/api/types";
import storedAapl from "./fixtures/run_compare_b3_aapl.json";
import storedMsft from "./fixtures/run_compare_msft_bp.json";
import gatedInvestor from "./fixtures/run_compare_gated_investor.json";
import legacy from "./fixtures/run_compare_auditor.json";
import excerpt from "./fixtures/excerpt_aapl_revenues.json";
import snippet from "./fixtures/snippet_msft.json";

// Real responses: excerpt_aapl_revenues.json is GET /api/facts/excerpt for AAPL's Q3 FY26
// revenue against the actual 10-Q (2026-09-25); snippet_msft.json is the search result
// agent B read for the page it cited in run 881a625e.

const aapl = storedAapl as unknown as Run;
const msft = storedMsft as unknown as Run;

function mockFetch(routes: Record<string, unknown>) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(url);
    const key = Object.keys(routes).find((k) => url.startsWith(k));
    return new Response(JSON.stringify(key ? routes[key] : []));
  }));
  return calls;
}

beforeEach(() => _resetTokens());
afterEach(() => vi.restoreAllMocks());

describe("U6 — a filing figure's source", () => {
  const revenue = aapl.facts!.a.find((f) => f.concept === "Revenues")!;

  it("opens in place, shows the row as filed, and returns focus on Escape", async () => {
    const calls = mockFetch({ "/api/facts/excerpt": excerpt });
    render(<SourcesProvider value={sourcesOf(aapl)}><FilingSource slot="a" fact={revenue} cited="$109.4 billion" /></SourcesProvider>);
    const button = screen.getByRole("button", { name: "Source" });
    fireEvent.click(button);
    const dialog = screen.getByRole("dialog", { name: /Revenue in the 10-Q/ });
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(within(dialog).getByText(/Finding this figure in the 10-Q/)).toBeInTheDocument();
    expect(await within(dialog).findByText("Total net sales")).toBeInTheDocument();
    expect(dialog.querySelector("mark")!.textContent).toBe("highlighted value: 109,417");
    expect(within(dialog).getByText(/\(in millions\)/)).toBeInTheDocument();
    expect(within(dialog).getByText("Matches the filing")).toBeInTheDocument();  // A's check passed
    expect(within(dialog).getByRole("link", { name: "Open the filing" }))
      .toHaveAttribute("href", "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627.htm");
    const url = new URL(calls.find((c) => c.startsWith("/api/facts/excerpt"))!, "http://x");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      cik: "0000320193", accn: "0000320193-26-000020", concept: "Revenues", end: "2026-06-27", start: "2026-03-29",
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /Show 2 other places it appears/ }));
    expect(dialog.querySelectorAll(".occurrence")).toHaveLength(3);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(button).toHaveFocus();
  });

  it("a point-in-time figure is looked up without a start date", async () => {
    const calls = mockFetch({ "/api/facts/excerpt": { ...excerpt, status: "not_found", best: null, occurrences: [],
      message: "The filing doesn't tag this figure for this period without a breakdown. Open the filing." } });
    const assets = aapl.facts!.a.find((f) => f.concept === "Assets")!;
    render(<SourcesProvider value={sourcesOf(aapl)}><FilingSource slot="a" fact={assets} /></SourcesProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Source" }));
    expect(await screen.findByText(/doesn't tag this figure/)).toBeInTheDocument();
    expect(new URL(calls.find((c) => c.startsWith("/api/facts"))!, "http://x").searchParams.has("start")).toBe(false);
  });

  it("isn't offered where it can't be answered or mustn't be shown", () => {
    // A run stored before B0 has no CIK in its trace; a figure the gate withholds gets no source.
    render(<SourcesProvider value={sourcesOf(legacy as unknown as Run)}>
      <FilingSource slot="a" fact={revenue} />
    </SourcesProvider>);
    expect(screen.queryByRole("button", { name: "Source" })).toBeNull();
    const investor = gatedInvestor as unknown as Run;
    const withheld = sourcesOf(investor);
    expect(withheld.withheldMetrics.has("release_year")).toBe(true);
  });

  it("every given figure in the run record has its own Source button, and so does each matrix cell", () => {
    mockFetch({});
    render(<RunDetailView run={aapl} flags={[]} scope="auditor" />);
    const laneA = screen.getByRole("article", { name: "Agent A" });
    expect(within(laneA).getAllByRole("button", { name: "Source" })).toHaveLength(aapl.facts!.a.filter((f) => !f.missing).length);
    const row = screen.getByRole("rowheader", { name: "Net income" }).closest("tr")!;
    expect(within(row).getAllByRole("button", { name: "Source" })).toHaveLength(2);
  });
});

describe("U6 — a web citation's source", () => {
  it("shows what the agent read at the page it cited, with its figures marked", async () => {
    mockFetch({ "/api/runs/": snippet });
    render(<RunDetailView run={msft} flags={[]} scope="auditor" />);
    const laneB = screen.getByRole("article", { name: "Agent B" });
    const pills = within(laneB).getByLabelText("Web pages agent B cited");
    fireEvent.click(within(pills).getAllByRole("button")[0]);
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/Found by agent B searching/)).toBeInTheDocument();
    expect(within(dialog).getByText("MSFT earnings 2026 Q3")).toBeInTheDocument();
    expect(within(dialog).getByText(/As recorded when the agent searched/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Open the page" })).toHaveAttribute("href", snippet.url);
    // This run halted (no comparison rows); B's own cited numbers are still marked.
    expect([...dialog.querySelectorAll("mark")].map((m) => m.textContent)).toContain("highlighted value: 82.9");
  });

  it("says plainly when the run predates recorded snippets", async () => {
    mockFetch({ "/api/runs/": { status: "not_found", url: "https://x.example",
      message: "This run was recorded before search snippets were kept, so only the URL is known." } });
    render(<SourcesProvider value={sourcesOf(aapl)}><WebSource url="https://x.example" label="x.example" slot="a" /></SourcesProvider>);
    fireEvent.click(screen.getByRole("button", { name: "x.example" }));
    expect(await screen.findByText(/recorded before search snippets were kept/)).toBeInTheDocument();
  });

  it("marks a figure by its number, not inside a longer one", () => {
    const text = "Revenue was $82.9 billion, up from 182.9; EPS 4.27 (4.275 adjusted).";
    const spans = figureSpans(text, ["$82.9 billion", "$4.27"]);
    expect(spans.map(([s, e]) => text.slice(s, e))).toEqual(["82.9", "4.27"]);
  });
});

describe("U6 — no source overlay without a run", () => {
  it("a citation outside a run is just a link", async () => {
    render(<WebSource url="https://y.example" label="y" slot={null} />);
    expect(screen.getByRole("link", { name: "y" })).toHaveAttribute("href", "https://y.example");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
