// The Analysis page (financial_agent/PLAN.md phase 2): ask for a signal, watch the steps, read the card.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AnalysisCard, AnalysisView } from "../src/views/AnalysisView";
import { headline, evidenceLabel, stepLine } from "../src/lib/analysis";
import { _resetTokens } from "../src/api/client";
import type { AnalysisRecord, AnalysisSummary } from "../src/api/types";

const OUT = {
  signal: "buy", horizon: "3-6m", conviction: "medium",
  key_points: [{ text: "The indicator panel is mixed to positive.", evidence: [{ call: 2 }] },
               { text: "No reason rests on a figure.", evidence: [{ judgment: true }] }],
  cited_figures: [{ name: "rsi_14", value: 58.2, unit: "index", ref: { call: 2 } }],
} as const;

function record(over: Partial<AnalysisRecord> = {}): AnalysisRecord {
  return {
    schema: "analysis-v1", run_id: "run-1", parent_run_id: null, created_at: "2026-10-26T01:00:00Z",
    request: { message: "Should I buy AAPL?", ticker: "AAPL", horizon: "3-6m", horizon_defaulted: true },
    agent: { kind: "builtin", model: "langchain:llama3.2", reasoning: "none", prompt_version: "analysis-v1",
             temperature: 0, seed: 42, label: null },
    bundle_hash: "sha256:abc", data_as_of: "2026-10-05", inputs_captured: true,
    tool_calls: [{ seq: 1, slot: "a", tool: "describe_data", args: {}, result: {}, result_sha256: "x" },
                 { seq: 2, slot: "a", tool: "get_indicators", args: {}, result: {}, result_sha256: "y" }],
    steps: [], attempts: [{ attempt: 1, parse_status: "SUCCESS", raw_text: "<thought_log>…", directive_version: "analysis-v1" }],
    corrective_text: null, output: JSON.parse(JSON.stringify(OUT)) as AnalysisRecord["output"],
    conclusion: "On this data the signal is a buy over three to six months.", thought_log: "I read call 2.",
    issues: [], halt_reason: null, status: "ok", ...over,
  };
}

const STEPS = [
  { seq: 1, kind: "data", label: "Data frozen", status: "ok", detail: "prices as of 2026-10-05, SEC filing facts", duration_ms: 3 },
  { seq: 2, kind: "tool", label: "describe_data", status: "ok", detail: null, duration_ms: 1, call: 1 },
  { seq: 3, kind: "llm", label: "Attempt 1", status: "parse_failure", detail: "the reply has no <signal> block", duration_ms: 9, attempt: 1 },
  { seq: 4, kind: "llm", label: "Attempt 2", status: "ok", detail: null, duration_ms: 9, attempt: 2 },
];

const sse = (events: [string, unknown][]) =>
  new Response(events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join(""),
               { status: 200, headers: { "Content-Type": "text/event-stream" } });
const json = (body: unknown, init?: ResponseInit) => new Response(JSON.stringify(body), init);

function stubFetch(handlers: { stream?: () => Response; history?: AnalysisSummary[]; open?: AnalysisRecord }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url === "/api/auth/token") return json({ access_token: "t" });
    if (url === "/api/analysis/stream") return handlers.stream!();
    if (url === "/api/analysis") return json(handlers.history ?? []);
    if (url.startsWith("/api/analysis/")) return handlers.open ? json(handlers.open) : json({ detail: "no" }, { status: 404 });
    return json({});
  }));
  return calls;
}

beforeEach(() => _resetTokens());
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("the wording", () => {
  it("says the letter, the word, the horizon with its default label, and the conviction", () => {
    expect(headline(record())).toBe("B · Buy · 3–6 months (default horizon) · medium conviction");
    const named = record({ request: { ...record().request, horizon_defaulted: false } });
    expect(headline(named)).toBe("B · Buy · 3–6 months · medium conviction");
    expect(headline(record({ output: null }))).toBe("No signal");
  });

  it("names the tool behind a call reference, and calls a judgment a judgment", () => {
    const r = record();
    expect(evidenceLabel({ call: 2 }, r.tool_calls)).toBe("call 2 · get_indicators");
    expect(evidenceLabel({ call: 9 }, r.tool_calls)).toBe("call 9");
    expect(evidenceLabel({ judgment: true }, r.tool_calls)).toBe("judgment");
    expect(evidenceLabel({ chunk: "d:1" }, r.tool_calls)).toBe("your document");
  });

  it("says a retry is a retry, and a refused tool call is refused", () => {
    expect(stepLine(STEPS[2] as never)).toBe("Attempt 1: the reply broke the format (the reply has no <signal> block)");
    expect(stepLine(STEPS[3] as never)).toBe("Attempt 2: the reply passed the format check");
    expect(stepLine({ seq: 1, kind: "tool", label: "get_prices", status: "error", detail: "last must be 1-260", duration_ms: 1 }))
      .toBe("Asked for get_prices: refused (last must be 1-260)");
  });
});

describe("AnalysisView", () => {
  it("streams the steps, then shows the stored record as a card that says it is not verified", async () => {
    const calls = stubFetch({ stream: () => sse([
      ["run_started", { run_id: "run-1", ticker: "AAPL" }],
      ...STEPS.map((s) => ["step", s] as [string, unknown]),
      ["result", record()],
    ]) });
    render(<AnalysisView scope="auditor" />);
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Should I buy AAPL?" } });
    fireEvent.click(screen.getByRole("button", { name: "Get a signal" }));

    const card = await screen.findByRole("article");
    expect(within(card).getByText("B · Buy · 3–6 months (default horizon) · medium conviction")).toBeInTheDocument();
    expect(within(card).getByText("Not verified")).toBeInTheDocument();
    expect(within(card).getByText(/not investment advice/)).toBeInTheDocument();
    expect(within(card).getAllByText("call 2 · get_indicators").length).toBeGreaterThan(0);
    expect(within(card).getByText("judgment")).toBeInTheDocument();
    expect(within(card).getByText(/has not been checked against the data/)).toBeInTheDocument();
    expect(within(card).getByText("AAPL · data as of 2026-10-05")).toBeInTheDocument();
    expect(within(card).getByText(/langchain:llama3.2 · no native reasoning/)).toBeInTheDocument();

    const post = calls.find((c) => c.url === "/api/analysis/stream")!;
    expect(JSON.parse(post.init!.body as string)).toEqual({ message: "Should I buy AAPL?" });
    expect((post.init!.headers as Record<string, string>).Authorization).toBe("Bearer t");
  });

  it("shows a retry as a retry while the run is still going", async () => {
    // A stream that stops before its result: the steps stay on screen, marked as lost.
    stubFetch({ stream: () => sse([["run_started", { ticker: "AAPL" }], ...STEPS.map((s) => ["step", s] as [string, unknown])]) });
    render(<AnalysisView scope="auditor" />);
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Should I buy AAPL?" } });
    fireEvent.click(screen.getByRole("button", { name: "Get a signal" }));
    expect(await screen.findByText(/Attempt 1: the reply broke the format/)).toBeInTheDocument();
    expect(screen.getByText(/Attempt 2: the reply passed the format check/)).toBeInTheDocument();
    expect(await screen.findByText(/Connection lost/)).toBeInTheDocument();
  });

  it("explains a refusal in the server's words, and keeps what was typed", async () => {
    stubFetch({ stream: () => json({ detail: "I couldn't find a listed company in that." }, { status: 422 }) });
    render(<AnalysisView scope="auditor" />);
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "tell me a joke" } });
    fireEvent.click(screen.getByRole("button", { name: "Get a signal" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("I couldn't find a listed company in that.");
    expect(screen.getByLabelText("Your question")).toHaveValue("tell me a joke");
    expect(screen.queryByRole("article")).toBeNull();
  });

  it("says why when price data is unavailable (503)", async () => {
    stubFetch({ stream: () => json({ detail: "Price data is unavailable: ALPHAVANTAGE_API_KEY is not set." }, { status: 503 }) });
    render(<AnalysisView scope="auditor" />);
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Should I buy AAPL?" } });
    fireEvent.click(screen.getByRole("button", { name: "Get a signal" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ALPHAVANTAGE_API_KEY is not set");
  });

  it("shows a failed run's error from the stream", async () => {
    stubFetch({ stream: () => sse([["run_started", { ticker: "AAPL" }], ["error", { error: "PriceFetchError: Alpha Vantage rate limit reached" }]]) });
    render(<AnalysisView scope="auditor" />);
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Should I buy AAPL?" } });
    fireEvent.click(screen.getByRole("button", { name: "Get a signal" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("rate limit");
  });

  it("lists earlier signals and opens one", async () => {
    const summary: AnalysisSummary = { ...record(), steps: undefined, tool_calls: undefined } as never;
    stubFetch({ history: [summary], open: record() });
    render(<AnalysisView scope="auditor" />);
    const item = await screen.findByRole("button", { name: /AAPL B · Buy/ });
    fireEvent.click(item);
    expect(await screen.findByText("B · Buy · 3–6 months (default horizon) · medium conviction")).toBeInTheDocument();
  });

  it("does not send a question that is too short", () => {
    stubFetch({});
    render(<AnalysisView scope="auditor" />);
    expect(screen.getByRole("button", { name: "Get a signal" })).toBeDisabled();
  });

  it("asks for the history at the viewer's scope", async () => {
    const calls = stubFetch({ history: [] });
    render(<AnalysisView scope="investor" />);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/analysis")).toBe(true));
    expect(await screen.findByText("None yet.")).toBeInTheDocument();
  });
});

describe("AnalysisCard", () => {
  it("shows a halted run as no signal, with its attempts, and no thinking at all", () => {
    render(<AnalysisCard scope="auditor" record={record({
      output: null, conclusion: null, thought_log: null, status: "halted", halt_reason: "both replies broke the format",
      attempts: [{ attempt: 1, parse_status: "PARSE_FAILURE", raw_text: "not the format", directive_version: "analysis-v1" },
                 { attempt: 2, parse_status: "HALT", raw_text: "still not", directive_version: "corrective" }],
    })} />);
    expect(screen.getByText("No signal")).toBeInTheDocument();
    expect(screen.getByText(/broke the required format twice/)).toBeInTheDocument();
    expect(screen.getByText("Attempt 1: broke the format")).toBeInTheDocument();
    expect(screen.getByText(/Attempt 2: broke the format again, so the run stopped/)).toBeInTheDocument();
    expect(screen.getByText("not the format")).toBeInTheDocument();
    expect(screen.getByText("Not verified")).toBeInTheDocument();
  });

  it("withholds the working notes and raw replies at investor scope and says so", () => {
    render(<AnalysisCard scope="investor" record={record({
      thought_log: null,
      attempts: [{ attempt: 1, parse_status: "SUCCESS", raw_text: null, directive_version: "analysis-v1", withheld: true }],
    })} />);
    expect(screen.getByText("Withheld at investor scope.")).toBeInTheDocument();
    expect(screen.getByText(/reply withheld at investor scope/)).toBeInTheDocument();
    expect(screen.getByText("B · Buy · 3–6 months (default horizon) · medium conviction")).toBeInTheDocument();
  });

  it("labels a native-reasoning model's notes as thinking and a plain one's as working notes", () => {
    const { rerender } = render(<AnalysisCard scope="auditor" record={record()} />);
    expect(screen.getByText("Working notes")).toBeInTheDocument();
    rerender(<AnalysisCard scope="auditor" record={record({ agent: { ...record().agent, reasoning: "native" } })} />);
    expect(screen.getByText("Thinking")).toBeInTheDocument();
  });

  it("lists what was adjusted instead of hiding it", () => {
    render(<AnalysisCard scope="auditor" record={record({ issues: ["conviction 'certain' is not one of low, medium, high; dropped"] })} />);
    expect(screen.getByText(/Adjusted/)).toBeInTheDocument();
    expect(screen.getByText(/conviction 'certain'/)).toBeInTheDocument();
  });
});
