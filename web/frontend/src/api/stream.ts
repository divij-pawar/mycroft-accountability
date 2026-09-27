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

export function streamCompare(
  scope: Scope, body: CompareRequestBody, onEvent: (e: StreamEvent) => void, signal?: AbortSignal,
): Promise<"done" | "lost"> {
  return streamRun("/api/compare/stream", scope, body, onEvent, signal);
}

/** Any of the stream routes: /api/compare/stream or /api/chat/stream (same event protocol). */
export async function streamRun(
  path: "/api/compare/stream" | "/api/chat/stream",
  scope: Scope,
  body: CompareRequestBody | ChatRequestBody,
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
    try { detail = JSON.stringify(((await res.json()) as { detail?: unknown }).detail ?? ""); } catch { /* no body */ }
    throw new Error(`The run wasn't started (HTTP ${res.status})${detail ? `: ${detail}` : ""}`);
  }
  let finalSeen = false;
  const parser = createSSEParser((e) => {
    if (e.event === "result" || e.event === "error") finalSeen = true;
    onEvent(e);
  });
  const reader = res.body.getReader();
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
