import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { useLiveRun } from "../src/state/useLiveRun";
import { LiveList, WatchView } from "../src/views/WatchView";
import { _resetTokens } from "../src/api/client";

// Watching a run the app didn't start: an agent session (Claude Code, Codex, Cursor) driving
// it over MCP. GET /api/runs/{id}/live replays the run's events; a session lane says it is
// waiting for the session; a watched run ends with a summary, and the stored record opens.

const enc = new TextEncoder();
const sse = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const json = (body: unknown) => new Response(JSON.stringify(body));
const PRODUCERS = {
  a: { agent_id: "financial", role: "PRODUCER A", model: "session: claude-code 2.1", concepts: [], overridden: false, session: true },
  b: { agent_id: "earnings", role: "PRODUCER B", model: "langchain:llama3.2", concepts: [], overridden: false, session: false },
  same_model: false,
};

beforeEach(() => { _resetTokens(); window.location.hash = ""; });
afterEach(() => vi.restoreAllMocks());

function streaming(events: string[], close: boolean) {
  return new Response(new ReadableStream<Uint8Array>({
    start(c) { for (const e of events) c.enqueue(enc.encode(e)); if (close) c.close(); },
  }));
}

describe("watching a session-driven run", () => {
  it("follows the live stream and says it is waiting for the agent session", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      urls.push(url);
      if (url === "/api/auth/token") return json({ access_token: "t" });
      return streaming([
        sse("run_started", { run_id: "r1", mode: "ticker", ticker: "AAPL", producers: PRODUCERS, started_by: "session: claude-code 2.1" }),
        sse("step_started", { seq: 1, phase: "agent_a", label: "LLM attempt 1", kind: "llm", attempt: 1,
                              started_at: "2026-10-05T00:00:00Z", duration_ms: null, status: "ok" }),
      ], false);
    }));
    const { result } = renderHook(() => useLiveRun());
    const { rerender } = render(<WatchView runId="r1" live={result.current} scope="auditor" />);
    await waitFor(() => expect(result.current.state.status).toBe("running"));
    rerender(<WatchView runId="r1" live={result.current} scope="auditor" />);
    expect(urls).toContain("/api/runs/r1/live");
    expect(screen.getByText(/Waiting for the agent session/)).toBeInTheDocument();
    expect(screen.getByText("session: claude-code 2.1")).toBeInTheDocument();
  });

  it("opens the stored record when the watched run ends", async () => {
    const record = { run_id: "r1", ticker: "AAPL", halted: false, reasoning_objects: [], session: { run_id: "r1" } };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/auth/token") return json({ access_token: "t" });
      if (url === "/api/runs/r1") return json(record);
      return streaming([
        sse("run_started", { run_id: "r1", mode: "ticker", ticker: "AAPL", producers: PRODUCERS }),
        sse("result", { run_id: "r1", halted: false, error: null, gate_status: "NO_DECISION_NEEDED", stored: true }),
      ], true);
    }));
    const onStored = vi.fn();
    const { result } = renderHook(() => useLiveRun(onStored));
    result.current.watch("auditor", "r1");
    await waitFor(() => expect(onStored).toHaveBeenCalledWith(record));
  });

  it("says plainly when the run isn't in progress any more", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      url === "/api/auth/token" ? json({ access_token: "t" }) : new Response("{}", { status: 404 })));
    const { result } = renderHook(() => useLiveRun());
    result.current.watch("auditor", "gone");
    await waitFor(() => expect(result.current.state.status).toBe("failed"));
    expect(result.current.state.error).toMatch(/isn't in progress any more/);
  });
});

describe("runs in progress", () => {
  it("lists runs started by anyone, linked to their watch page", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json([
      { run_id: "r1abcdef00", started_by: "session: claude-code 2.1", mode: "ticker", ticker: "AAPL", subject: null },
    ])));
    render(<LiveList />);
    const link = await screen.findByRole("link", { name: "AAPL" });
    expect(link.getAttribute("href")).toBe("#/live/r1abcdef00");
    expect(screen.getByText(/started by session: claude-code 2.1/)).toBeInTheDocument();
  });

  it("says when nothing is running", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json([])));
    render(<LiveList />);
    expect(await screen.findByText("Nothing is running right now.")).toBeInTheDocument();
  });
});
