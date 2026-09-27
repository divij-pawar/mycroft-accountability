import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import RAW from "./fixtures/stream_compare_aapl_2026-09-24.txt?raw";
import { createSSEParser, type StreamEvent } from "../src/api/stream";
import { _resetTokens } from "../src/api/client";
import { initialLive, laneLine, laneSources, liveReducer, ranSequentially, type LiveState } from "../src/state/liveRun";
import { useLiveRun } from "../src/state/useLiveRun";
import { CompareForm, LiveRunView } from "../src/views/CompareView";
import { App } from "../src/App";

// The stream fixture is a real /api/compare/stream response for AAPL, captured live on
// 2026-09-24 (both agents searched, both finished, the run was stored). Replaying it in
// arbitrary chunks is how a network actually delivers it.


function allEvents(): StreamEvent[] {
  const out: StreamEvent[] = [];
  const p = createSSEParser((e) => out.push(e));
  p.push(RAW);
  return out;
}

const EVENTS = allEvents();
const upTo = (pred: (e: StreamEvent) => boolean) => EVENTS.slice(0, EVENTS.findIndex(pred) + 1);
const stateAfter = (events: StreamEvent[]): LiveState =>
  liveReducer(liveReducer(initialLive, { type: "start", title: "AAPL" }), { type: "events", events });

describe("SSE parser", () => {
  it("delivers the same events however the bytes are split", () => {
    for (const size of [1, 7, 64, 4096]) {
      const got: StreamEvent[] = [];
      const p = createSSEParser((e) => got.push(e));
      for (let i = 0; i < RAW.length; i += size) p.push(RAW.slice(i, i + size));
      expect(got).toEqual(EVENTS);
    }
    expect(EVENTS.map((e) => e.event)).toContain("result");
  });

  it("handles CRLF, comments, multi-line data, and drops an unterminated tail", () => {
    const got: StreamEvent[] = [];
    const p = createSSEParser((e) => got.push(e));
    p.push(": keep-alive\r\n\r\nevent: note\r\ndata: line one\r\ndata: line two\r\n\r\nevent: result\ndata: {\"x\"");
    p.end();
    expect(got).toEqual([{ event: "note", data: "line one\nline two" }]);
  });
});

describe("live run reducer, over the real stream", () => {
  it("ends done, with both agents finished and the stored result", () => {
    const s = stateAfter(EVENTS);
    expect(s.status).toBe("done");
    expect(s.result?.session).toBeTruthy();
    expect(laneLine(s, "a")).toEqual({ kind: "finished", halted: false });
    expect(Object.values(s.steps).filter((st) => st.phase === "shared")).toHaveLength(4);
    expect(s.announcements).toEqual(expect.arrayContaining(["Run started", "Agent A finished", "Agent B finished", "Comparison ready"]));
  });

  it("shows thinking, then searching with the query, from real step events only", () => {
    const thinking = stateAfter(upTo((e) => e.event === "step_started" && (e.data as { phase: string }).phase === "agent_b"));
    expect(laneLine(thinking, "b")).toMatchObject({ kind: "thinking", attempt: 1 });
    const searching = stateAfter(upTo((e) => (e.data as { tool_phase?: string; phase?: string }).tool_phase === "started"
                                          && (e.data as { phase: string }).phase === "agent_b"));
    expect(laneLine(searching, "b")).toMatchObject({ kind: "searching", query: "AAPL financial news" });
    const found = stateAfter(upTo((e) => (e.data as { tool_phase?: string; phase?: string }).tool_phase === "finished"
                                      && (e.data as { phase: string }).phase === "agent_b"));
    expect(laneSources(found, "b").length).toBeGreaterThan(0);
    expect(laneLine(found, "b")).toMatchObject({ kind: "thinking" });
  });

  it("says when the agents actually ran one at a time — and not otherwise", () => {
    expect(ranSequentially(stateAfter(EVENTS))).toBe(false); // this capture ran them concurrently
    const seq: StreamEvent[] = [
      { event: "step_finished", data: { seq: 1, phase: "agent_a", label: "LLM attempt 1", kind: "llm", started_at: "2026-09-25T10:00:00.000Z", duration_ms: 30000 } },
      { event: "agent_finished", data: { agent: "a", halted: false, conclusion: "x" } },
      { event: "step_started", data: { seq: 2, phase: "agent_b", label: "LLM attempt 1", kind: "llm", started_at: "2026-09-25T10:00:30.500Z", duration_ms: null } },
    ];
    expect(ranSequentially(stateAfter(seq))).toBe(true);
  });

  it("a late step_started never overwrites a finished step", () => {
    const fin = { seq: 9, phase: "agent_a", label: "x", started_at: "2026-09-25T10:00:00Z", duration_ms: 5 };
    const s = stateAfter([{ event: "step_finished", data: fin }, { event: "step_started", data: { ...fin, duration_ms: null } }]);
    expect(s.steps[9].duration_ms).toBe(5);
  });
});

describe("LiveRunView", () => {
  it("draws the shared fetch once above the lanes and each lane's current activity", () => {
    const s = stateAfter(upTo((e) => (e.data as { tool_phase?: string; phase?: string }).tool_phase === "started"
                                   && (e.data as { phase: string }).phase === "agent_b"));
    render(<LiveRunView state={s} onReset={() => {}} />);
    expect(screen.getByRole("heading", { name: "Shared: one fetch, both agents" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /SEC company facts \(CIK 0000320193\)/ })).toBeInTheDocument();
    const b = screen.getByRole("article", { name: "Agent B" });
    expect(within(b).getByText(/Searching/)).toBeInTheDocument();
    expect(within(b).getByText("AAPL financial news")).toBeInTheDocument();
    expect(within(screen.getByRole("article", { name: "Agent A" })).getByText(/Thinking/)).toBeInTheDocument();
  });

  it("records announcements for the app's live region and explains a lost connection", () => {
    const lost = liveReducer(stateAfter(upTo((e) => e.event === "agent_finished")), { type: "lost" });
    expect(lost.announcements.slice(-1)).toEqual(["Connection lost"]);
    render(<LiveRunView state={lost} onReset={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent(/The run continues on the server/);
  });

  it("stops promising the run will appear once it plainly isn't coming", () => {
    const lost = liveReducer(stateAfter(upTo((e) => e.event === "agent_finished")), { type: "lost", at: Date.now() - 11 * 60 * 1000 });
    render(<LiveRunView state={lost} onReset={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent(/Still not stored 11 minutes after the connection dropped/);
    expect(screen.getByRole("button", { name: "Start another run" })).toBeInTheDocument();
  });
});

describe("CompareForm", () => {
  it("only starts with a valid ticker, and sends exactly what was entered", () => {
    const onStart = vi.fn();
    render(<CompareForm onStart={onStart} disabled={false} />);
    const run = screen.getByRole("button", { name: "Run both agents" });
    fireEvent.change(screen.getByLabelText("Ticker"), { target: { value: "aapl 1" } });
    expect(run).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Ticker"), { target: { value: "aapl" } });
    fireEvent.click(run);
    expect(onStart).toHaveBeenCalledWith({ ticker: "AAPL" }, "AAPL");
    fireEvent.click(screen.getByRole("radio", { name: /Any question/ }));
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "When was Inception released?" } });
    fireEvent.click(run);
    expect(onStart).toHaveBeenLastCalledWith({ subject: "When was Inception released?", context: "" }, "When was Inception released?");
  });
});

describe("useLiveRun, end to end with a streamed response", () => {
  beforeEach(() => _resetTokens());
  afterEach(() => vi.restoreAllMocks());

  function streamOf(text: string, chunk = 97) {
    const enc = new TextEncoder();
    return new ReadableStream<Uint8Array>({
      start(c) {
        for (let i = 0; i < text.length; i += chunk) c.enqueue(enc.encode(text.slice(i, i + chunk)));
        c.close();
      },
    });
  }

  it("streams the run and hands the stored record over", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(url);
      if (url === "/api/auth/token") return new Response(JSON.stringify({ access_token: "t" }));
      expect((init!.headers as Record<string, string>).Authorization).toBe("Bearer t");
      return new Response(streamOf(RAW), { headers: { "Content-Type": "text/event-stream" } });
    }));
    const onStored = vi.fn();
    const { result } = renderHook(() => useLiveRun(onStored));
    act(() => result.current.start("auditor", { ticker: "AAPL" }, "AAPL"));
    await waitFor(() => expect(onStored).toHaveBeenCalledTimes(1));
    expect(onStored.mock.calls[0][0].run_id).toBe(result.current.state.runId);
    expect(calls).toContain("/api/compare/stream");
  });

  it("keeps updating in a window that never paints (requestAnimationFrame never fires)", async () => {
    vi.stubGlobal("requestAnimationFrame", () => 0); // found live: an undrawn window reports "visible" but never paints
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url === "/api/auth/token"
      ? new Response(JSON.stringify({ access_token: "t" }))
      : new Response(streamOf(RAW))));
    const onStored = vi.fn();
    const { result } = renderHook(() => useLiveRun(onStored));
    act(() => result.current.start("auditor", { ticker: "AAPL" }, "AAPL"));
    await waitFor(() => expect(onStored).toHaveBeenCalledTimes(1));
  });

  it("a stream that breaks before its result is 'lost', and the run is fetched once stored", async () => {
    const cut = RAW.slice(0, RAW.indexOf("event: agent_finished"));
    let stored = false;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/auth/token") return new Response(JSON.stringify({ access_token: "t" }));
      if (url === "/api/compare/stream") return new Response(streamOf(cut));
      if (!stored) { stored = true; return new Response("not yet", { status: 404 }); }
      return new Response(JSON.stringify({ run_id: "x", halted: false, reasoning_objects: [] }));
    }));
    const onStored = vi.fn();
    const { result } = renderHook(() => useLiveRun(onStored, 20));
    act(() => result.current.start("auditor", { ticker: "AAPL" }, "AAPL"));
    await waitFor(() => expect(result.current.state.status).toBe("lost"));
    await waitFor(() => expect(onStored).toHaveBeenCalledWith(expect.objectContaining({ run_id: "x" })));
  });
});

describe("App: a live run handed over to its record", () => {
  afterEach(() => { vi.restoreAllMocks(); window.location.hash = ""; });

  it("opens the stored record and still says so to a screen reader", async () => {
    _resetTokens();
    const enc = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(enc.encode(RAW)); c.close(); } });
    const result = EVENTS.find((e) => e.event === "result")!.data as { run_id: string };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/auth/token") return new Response(JSON.stringify({ access_token: "t" }));
      if (url === "/api/compare/stream") return new Response(body);
      if (url === `/api/runs/${result.run_id}`) return new Response(JSON.stringify(result));
      return new Response(JSON.stringify([]));
    }));
    window.location.hash = "/new";
    render(<App />);
    fireEvent.change(screen.getByLabelText("Ticker"), { target: { value: "AAPL" } });
    fireEvent.click(screen.getByRole("button", { name: "Run both agents" }));
    await waitFor(() => expect(window.location.hash).toBe(`#/runs/${result.run_id}`));
    await waitFor(() => expect(screen.getByTestId("announcer")).toHaveTextContent("Comparison ready; showing its record"));
  });
});
