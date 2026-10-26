// Live runs (U4): POST /api/compare/stream and read its text/event-stream.
//
// EventSource can't POST or send an Authorization header (SEC-02 needs the Bearer
// token), so this is fetch + a ReadableStream reader with its own small parser.
// The parser is incremental: a network chunk can end mid-line or mid-event, and
// an event is only delivered once its blank-line terminator has arrived.

import { authHeader } from "./client";
import type { Scope } from "./types";

export interface StreamEvent {
  event: string;
  data: unknown;
}

/** Feed it text as it arrives; it calls onEvent once per complete event. */
export function createSSEParser(onEvent: (e: StreamEvent) => void) {
  let buffer = "";
  const flush = (block: string) => {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue; // comment / keep-alive
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (!data.length) return;
    const text = data.join("\n");
    let parsed: unknown = text;
    try { parsed = JSON.parse(text); } catch { /* not JSON: deliver the text */ }
    onEvent({ event, data: parsed });
  };
  return {
    push(chunk: string) {
      buffer += chunk.replace(/\r\n?/g, "\n");
      let at: number;
      while ((at = buffer.indexOf("\n\n")) >= 0) {
        flush(buffer.slice(0, at));
        buffer = buffer.slice(at + 2);
      }
    },
    /** The stream closed: an unterminated trailing event is incomplete and dropped. */
    end() { buffer = ""; },
  };
}

export interface CompareRequestBody {
  ticker?: string;
  subject?: string;
  pairing?: "lenses" | "bull_bear";
  context?: string;
  agent_a_model?: string;
  agent_b_model?: string;
}

/**
 * Start a compare and deliver its events. Resolves "done" when the server closed
 * the stream after its final event, or "lost" if the connection broke first — in
 * which case the run is still going on the server (a closed tab can't cancel it).
 * Rejects only if the request itself was refused (HTTP error before streaming).
 */
export interface ChatRequestBody {
  message: string;
  context?: string;
}

export interface AnalysisRequestBody {
  message: string;
}

/** A signal for the ticker in the message: step events, then `result` with the record. */
export function streamAnalysis(
  scope: Scope, body: AnalysisRequestBody, onEvent: (e: StreamEvent) => void, signal?: AbortSignal,
): Promise<"done" | "lost"> {
  return streamRun("/api/analysis/stream", scope, body, onEvent, signal);
}

export function streamCompare(
  scope: Scope, body: CompareRequestBody, onEvent: (e: StreamEvent) => void, signal?: AbortSignal,
): Promise<"done" | "lost"> {
  return streamRun("/api/compare/stream", scope, body, onEvent, signal);
}

/** Any of the stream routes: /api/compare/stream or /api/chat/stream (same event protocol). */
export async function streamRun(
  path: "/api/compare/stream" | "/api/chat/stream" | "/api/analysis/stream",
  scope: Scope,
  body: CompareRequestBody | ChatRequestBody | AnalysisRequestBody,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<"done" | "lost"> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeader(scope)) },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = "";
    try {
      const d = ((await res.json()) as { detail?: unknown }).detail ?? "";
      detail = typeof d === "string" ? d : JSON.stringify(d);   // the server explains a refusal in words
    } catch { /* no body */ }
    throw new Error(`The run wasn't started (HTTP ${res.status})${detail ? `: ${detail}` : ""}`);
  }
  return readEvents(res.body, onEvent);
}

/**
 * Follow a run someone else started (GET /api/runs/{id}/live): every event so far, then new
 * ones. This is how a run driven by an agent session over MCP is watched; the events are the
 * same as a started stream's, so the live view reads them unchanged.
 */
export async function watchRun(
  runId: string, scope: Scope, onEvent: (e: StreamEvent) => void, signal?: AbortSignal,
): Promise<"done" | "lost"> {
  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/live`, {
    headers: { ...(await authHeader(scope)) }, signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(res.status === 404
      ? "That run isn't in progress any more. Open it from History."
      : `This run can't be watched (HTTP ${res.status}).`);
  }
  return readEvents(res.body, onEvent);
}

async function readEvents(body: ReadableStream<Uint8Array>, onEvent: (e: StreamEvent) => void): Promise<"done" | "lost"> {
  let finalSeen = false;
  const parser = createSSEParser((e) => {
    if (e.event === "result" || e.event === "error") finalSeen = true;
    onEvent(e);
  });
  const reader = body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    parser.push(decoder.decode());
  } catch {
    return "lost";
  } finally {
    parser.end();
  }
  return finalSeen ? "done" : "lost";
}
