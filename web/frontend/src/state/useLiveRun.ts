import { useCallback, useEffect, useReducer, useRef } from "react";
import { api } from "../api/client";
import { streamRun, type ChatRequestBody, type CompareRequestBody, type StreamEvent } from "../api/stream";
import type { Run, Scope } from "../api/types";
import { initialLive, liveReducer, type LiveState } from "./liveRun";

// Owns one live run for the whole app (it lives in App, not in the view), so the
// stream keeps going while the reviewer looks at History; the top bar shows it as
// "1 running". Events are applied in batches once per animation frame, so a burst
// of steps re-renders once, not once per event.

// Next frame, or 100 ms, whichever comes first — once. A window that isn't being
// drawn (minimised, behind another window) never fires requestAnimationFrame while
// still reporting itself "visible"; found live on 2026-09-25, when a run's events
// queued up unseen. The timer keeps the view (and the hand-over to the stored
// record) moving there too.
function frame(cb: () => void): void {
  let done = false;
  const once = () => { if (!done) { done = true; cb(); } };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(once);
  window.setTimeout(once, 100);
}

const POLL_MS = 5000;

export interface LiveRunControls {
  state: LiveState;
  start: (scope: Scope, body: CompareRequestBody, title: string) => void;
  /** A chat run (U9): one agent, the same stream protocol. */
  startChat: (scope: Scope, body: ChatRequestBody) => void;
  reset: () => void;
  running: boolean;
}

export function useLiveRun(onStored?: (run: Run) => void, pollMs = POLL_MS): LiveRunControls {
  const [state, dispatch] = useReducer(liveReducer, initialLive);
  const queue = useRef<StreamEvent[]>([]);
  const scheduled = useRef(false);
  const scopeRef = useRef<Scope>("auditor");

  const enqueue = useCallback((e: StreamEvent) => {
    queue.current.push(e);
    if (scheduled.current) return;
    scheduled.current = true;
    frame(() => {
      scheduled.current = false;
      const events = queue.current;
      queue.current = [];
      dispatch({ type: "events", events });
    });
  }, []);

  const run = useCallback((path: "/api/compare/stream" | "/api/chat/stream", scope: Scope,
                           body: CompareRequestBody | ChatRequestBody, title: string) => {
    scopeRef.current = scope;
    dispatch({ type: "start", title });
    streamRun(path, scope, body, enqueue)
      .then((outcome) => { if (outcome === "lost") frame(() => dispatch({ type: "lost" })); })
      .catch((err: Error) => dispatch({ type: "failed", error: err.message }));
  }, [enqueue]);
  const start = useCallback((scope: Scope, body: CompareRequestBody, title: string) =>
    run("/api/compare/stream", scope, body, title), [run]);
  const startChat = useCallback((scope: Scope, body: ChatRequestBody) =>
    run("/api/chat/stream", scope, body, body.message), [run]);

  // A lost connection doesn't stop the run on the server; when it lands in the
  // store, show it. Polls the one run id the stream announced — nothing else.
  useEffect(() => {
    if (state.status !== "lost" || !state.runId) return;
    const id = state.runId;
    const timer = window.setInterval(() => {
      api.run(id, scopeRef.current).then((run) => { window.clearInterval(timer); onStored?.(run); }).catch(() => { /* not stored yet */ });
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [state.status, state.runId, onStored, pollMs]);

  // A finished run that was persisted (it has a session) is shown from the store,
  // so what the reviewer sees is the record, redacted for their scope, with its gate.
  useEffect(() => {
    if (state.status === "done" && state.result?.session) onStored?.(state.result);
  }, [state.status, state.result, onStored]);

  const reset = useCallback(() => dispatch({ type: "reset" }), []);
  const running = state.status === "starting" || state.status === "running";
  return { state, start, startChat, reset, running };
}
