import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { ChatView, DirectiveView, LedgerView, SettingsView } from "../src/views/Pages";
import { RunDetailView } from "../src/views/RunDetail";
import { App } from "../src/App";
import { useLiveRun } from "../src/state/useLiveRun";
import { _resetTokens } from "../src/api/client";
import type { Run } from "../src/api/types";
import b5 from "./fixtures/run_compare_b5_auditor.json";

// U9 — the classic UI's remaining pages, now in the app, and the review export.

const CONFIG = { provider: "langchain", model: "llama3.2", temperature: 0, seed: 42, agent_id: "external",
                 confidence_score: 0.75, consistency_probe: false };
const LEDGER = {
  automated_tests: { total: 442, modules: [], error: null },
  live_model_tests: { run_on: "", model: "", caveats: [], tests: [] },
  known_issues: [
    { id: "x-open", severity: "high", area: "Model", title: "Hangs under load", detail: "Seen **five** times.", status: "OPEN", source: "RUN_LOG" },
    { id: "x-done", severity: "low", area: "UI", title: "Old bug", detail: "Fixed.", status: "RESOLVED", source: "RUN_LOG" },
  ],
  counts: { issues_total: 2, issues_open: 1, issues_critical: 0 },
  deployment_status: { state: "localhost only", detail: "Not deployable." },
};

function json(body: unknown, init?: ResponseInit) {
  return new Response(JSON.stringify(body), init);
}

beforeEach(() => { _resetTokens(); window.location.hash = ""; });
afterEach(() => vi.restoreAllMocks());

describe("Settings", () => {
  it("shows the server's settings and saves a change", async () => {
    const posted: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") { posted.push(JSON.parse(init.body as string)); return json({ ...CONFIG, seed: 7 }); }
      return json(CONFIG);
    }));
    render(<SettingsView scope="auditor" />);
    const seed = await screen.findByLabelText("Seed");
    fireEvent.change(seed, { target: { value: "7" } });
    fireEvent.blur(seed);
    await waitFor(() => expect(posted).toEqual([{ seed: 7 }]));
    expect(await screen.findByText("Saved seed")).toBeInTheDocument();
  });

  it("is read-only at investor scope", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(CONFIG)));
    render(<SettingsView scope="investor" />);
    expect(await screen.findByLabelText("Seed")).toBeDisabled();
    expect(screen.getByText(/Switch to auditor scope/)).toBeInTheDocument();
  });
});

describe("Directive and Honest Ledger pages", () => {
  it("shows the active directive in full with its version", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ version: "v1.5.2", text: "You are a verification agent…" })));
    render(<DirectiveView />);
    expect(await screen.findByText("v1.5.2")).toBeInTheDocument();
    expect(screen.getByText("You are a verification agent…")).toBeInTheDocument();
  });

  it("lists what is still open first, in plain words, and can show everything", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(LEDGER)));
    const onCount = vi.fn();
    render(<LedgerView onCount={onCount} />);
    expect(await screen.findByText("Hangs under load")).toBeInTheDocument();
    expect(onCount).toHaveBeenCalledWith(1);
    expect(screen.queryByText("Old bug")).toBeNull();
    expect(screen.getByText(/1 open/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Everything" }));
    expect(screen.getByText("Old bug")).toBeInTheDocument();
    expect(screen.getByText("Fixed")).toBeInTheDocument();
  });
});

describe("Chat", () => {
  it("starts a chat run on the chat stream with the question and context", async () => {
    const bodies: { url: string; body: unknown }[] = [];
    const enc = new TextEncoder();
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/auth/token") return json({ access_token: "t" });
      bodies.push({ url, body: JSON.parse(init!.body as string) });
      const stream = new ReadableStream<Uint8Array>({
        start(c) {
          c.enqueue(enc.encode('event: run_started\ndata: {"run_id": "c1", "mode": "chat", "subject": "q"}\n\n'));
          c.enqueue(enc.encode('event: step_started\ndata: {"seq": 1, "phase": "chat", "label": "LLM attempt 1", "kind": "llm", "attempt": 1, "started_at": "2026-09-27T00:00:00Z", "duration_ms": null}\n\n'));
        },
      });
      return new Response(stream);
    }));
    const { result } = renderHook(() => useLiveRun());
    const { rerender } = render(<ChatView live={result.current} scope="auditor" />);
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "What was revenue?" } });
    fireEvent.click(screen.getByRole("button", { name: "Add context" }));
    fireEvent.change(screen.getByLabelText(/Context/), { target: { value: "Revenues: 1" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ url: "/api/chat/stream", body: { message: "What was revenue?", context: "Revenues: 1" } });
    await waitFor(() => expect(result.current.state.status).toBe("running"));
    rerender(<ChatView live={result.current} scope="auditor" />);
    expect(screen.getByText(/Thinking…/)).toBeInTheDocument();
  });
});

describe("Chat hand-over", () => {
  it("announces an answer, not a comparison", async () => {
    const enc = new TextEncoder();
    const run = { run_id: "c1", halted: false, reasoning_objects: [], session: { run_id: "c1" }, conclusion: "1998" };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/auth/token") return json({ access_token: "t" });
      if (url === "/api/chat/stream") {
        return new Response(new ReadableStream<Uint8Array>({ start(c) {
          c.enqueue(enc.encode(`event: result
data: ${JSON.stringify(run)}

`)); c.close();
        } }));
      }
      if (url === "/api/runs/c1") return json(run);
      if (url === "/api/directive") return json({ version: "v1.5.2", text: "t" });
      return json([]);
    }));
    window.location.hash = "/chat";
    render(<App />);
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "When?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByTestId("announcer")).toHaveTextContent("Answer ready; showing its record"));
  });
});

describe("Download review", () => {
  it("downloads the Markdown review at the viewer's scope, JSON only under Technical details", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      urls.push(url);
      if (url === "/api/auth/token") return json({ access_token: "t" });
      return new Response("# Review", { headers: { "content-disposition": 'attachment; filename="review-8ecb0922-investor.md"' } });
    }));
    const created = vi.fn(() => "blob:x");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() }));
    const run = b5 as unknown as Run;
    render(<RunDetailView run={run} flags={[]} scope="investor" />);
    fireEvent.click(screen.getByRole("button", { name: "Download review" }));
    await waitFor(() => expect(created).toHaveBeenCalled());
    expect(urls).toContain(`/api/runs/${run.run_id}/export.md`);
    const raw = screen.getByText("Technical details: the raw record").closest("details")!;
    expect(raw.open).toBe(false);
    expect(within(raw).getByRole("button", { name: "Download the audit record (JSON)" })).toBeInTheDocument();
  });
});

describe("App navigation", () => {
  it("reaches every page from the top bar, and says when the server is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/directive") return json({ version: "v1.5.2", text: "t" });
      if (url === "/api/self-report") return json(LEDGER);
      return json([]);
    }));
    render(<App />);
    const nav = screen.getByRole("navigation", { name: "Pages" });
    expect(await within(nav).findByText("Honest Ledger (1 open)")).toBeInTheDocument();
    expect(await within(nav).findByText("Directive v1.5.2")).toBeInTheDocument();
    expect(within(nav).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["#/chat", "#/ledger", "#/directive", "#/settings"]);
    expect(screen.queryByText("Server unreachable")).toBeNull();
  });

  it("shows the server as unreachable when it is", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    render(<App />);
    expect(await screen.findByText("Server unreachable")).toBeInTheDocument();
  });
});
