// One live run = one reducer fed by stream events (U4). Everything shown while a
// run is in progress is derived from real server events: a step is "running"
// only because a step_started arrived without its step_finished, and an elapsed
// time is measured from the step's own server-side started_at. Nothing here
// simulates progress (the plan's "honest liveness" rule).

import type { Producer, Run, Step } from "../api/types";
import type { StreamEvent } from "../api/stream";

export type Slot = "a" | "b";
export type LiveStatus = "idle" | "starting" | "running" | "done" | "failed" | "lost";

export interface AgentLive {
  finished: boolean;
  halted: boolean;
  conclusion: string | null;
}

export interface LiveState {
  status: LiveStatus;
  runId: string | null;
  mode: "ticker" | "subject" | null;
  title: string | null;
  producers: { a: Producer; b: Producer; same_model: boolean; shared_concepts?: string[] } | null;
  steps: Record<number, Step>;
  agents: Record<Slot, AgentLive>;
  result: Run | null;
  error: string | null;
  /** When the connection dropped (client clock), to say honestly how long it has been. */
  lostAt: number | null;
  /** Polite screen-reader announcements, oldest first. */
  announcements: string[];
}

export const initialLive: LiveState = {
  status: "idle", runId: null, mode: null, title: null, producers: null, steps: {},
  agents: { a: { finished: false, halted: false, conclusion: null }, b: { finished: false, halted: false, conclusion: null } },
  result: null, error: null, lostAt: null, announcements: [],
};

export type LiveAction =
  | { type: "start"; title: string }
  | { type: "events"; events: StreamEvent[] }
  | { type: "failed"; error: string }
  | { type: "lost"; at?: number }
  | { type: "reset" };

const say = (s: LiveState, text: string): LiveState => ({ ...s, announcements: [...s.announcements, text] });

function apply(s: LiveState, e: StreamEvent): LiveState {
  const d = (e.data ?? {}) as Record<string, unknown>;
  switch (e.event) {
    case "run_started":
      return say({
        ...s, status: "running", runId: String(d.run_id),
        mode: d.mode as LiveState["mode"],
        title: (d.ticker as string) || (d.subject as string) || s.title,
        producers: (d.producers as LiveState["producers"]) ?? null,
      }, "Run started");
    case "step_started":
    case "step_finished": {
      const step = d as unknown as Step;
      // A late step_started never overwrites the finished snapshot of the same step.
      const prev = s.steps[step.seq];
      if (e.event === "step_started" && prev && prev.duration_ms !== null) return s;
      return { ...s, steps: { ...s.steps, [step.seq]: step } };
    }
    case "agent_finished": {
      const slot = d.agent as Slot;
      const halted = Boolean(d.halted);
      return say({
        ...s, agents: { ...s.agents, [slot]: { finished: true, halted, conclusion: (d.conclusion as string) ?? null } },
      }, `Agent ${slot.toUpperCase()} ${halted ? "halted" : "finished"}`);
    }
    case "result": {
      const run = d as unknown as Run;
      return say({ ...s, status: "done", result: run, runId: run.run_id ?? s.runId },
        run.error ? `Run ended: ${run.error}` : "Comparison ready");
    }
    case "error":
      return say({ ...s, status: "failed", error: String(d.error ?? "The run failed") }, "The run failed");
    default:
      return s;
  }
}

export function liveReducer(s: LiveState, a: LiveAction): LiveState {
  switch (a.type) {
    case "start":
      return { ...initialLive, status: "starting", title: a.title };
    case "events":
      return a.events.reduce(apply, s);
    case "failed":
      return say({ ...s, status: "failed", error: a.error }, "The run failed");
    case "lost":
      return s.status === "done" || s.status === "failed" ? s
        : say({ ...s, status: "lost", lostAt: a.at ?? Date.now() }, "Connection lost");
    case "reset":
      return initialLive;
  }
}

// ── Derived views ──────────────────────────────────────────────────────────────

export const stepsOf = (s: LiveState, phase: string): Step[] =>
  Object.values(s.steps).filter((st) => st.phase === phase).sort((x, y) => x.seq - y.seq);

const finishedAt = (st: Step): number | null =>
  st.duration_ms === null || !st.started_at ? null : Date.parse(st.started_at) + st.duration_ms;

export type LaneLine =
  | { kind: "waiting" }
  | { kind: "thinking"; since: string; attempt: number }
  | { kind: "searching"; query: string | null; since: string | undefined }
  | { kind: "extracting"; since: string | undefined }
  | { kind: "between"; label: string }
  | { kind: "finished"; halted: boolean };

/** What this agent is doing right now, from the latest of its steps. */
export function laneLine(s: LiveState, slot: Slot): LaneLine {
  const agent = s.agents[slot];
  if (agent.finished) return { kind: "finished", halted: agent.halted };
  return phaseLine(s, `agent_${slot}`);
}

/** The same reading for any phase — "chat" for a chat run, which has one agent. */
export function phaseLine(s: LiveState, phase: string): LaneLine {
  const steps = stepsOf(s, phase);
  if (!steps.length) return { kind: "waiting" };
  // A search is open while its "started" note has no "finished" note after it.
  const tools = steps.filter((st) => st.kind === "tool");
  const lastTool = tools[tools.length - 1];
  if (lastTool && lastTool.tool_phase === "started") {
    return { kind: "searching", query: lastTool.query ?? null, since: lastTool.started_at };
  }
  // B4 option 1: the second call that reads the finished answer for a grade.
  const extracting = steps.find((st) => st.kind === "extract" && st.duration_ms === null);
  if (extracting) return { kind: "extracting", since: extracting.started_at };
  const llm = steps.filter((st) => st.kind === "llm");
  const open = [...llm].reverse().find((st) => st.duration_ms === null);
  if (open) return { kind: "thinking", since: open.started_at ?? "", attempt: open.attempt ?? 1 };
  return { kind: "between", label: steps[steps.length - 1].label };
}

/** Every URL this agent's searches returned, in order, with the page title when recorded. */
export function laneSources(s: LiveState, slot: Slot): { url: string; title: string | null; query: string | null }[] {
  const out: { url: string; title: string | null; query: string | null }[] = [];
  const seen = new Set<string>();
  for (const st of stepsOf(s, `agent_${slot}`)) {
    if (st.kind !== "tool" || st.tool_phase !== "finished") continue;
    const titles = new Map((st.results ?? []).map((r) => [r.url, r.title]));
    for (const url of st.urls ?? []) {
      if (seen.has(url)) continue;
      seen.add(url);
      out.push({ url, title: titles.get(url) ?? null, query: st.query ?? null });
    }
  }
  return out;
}

/**
 * True when agent B's first model call started only after agent A's last step
 * ended — the run went one agent at a time (CROSS_AGENT_MAX_CONCURRENCY=1, or a
 * local model server that serialises). Said out loud so two lanes side by side
 * never imply parallelism that didn't happen.
 */
export function ranSequentially(s: LiveState): boolean {
  const a = stepsOf(s, "agent_a");
  const bFirst = stepsOf(s, "agent_b").find((st) => st.kind === "llm");
  if (!a.length || !bFirst?.started_at || !s.agents.a.finished) return false;
  const aEnd = Math.max(...a.map((st) => finishedAt(st) ?? 0));
  return Date.parse(bFirst.started_at) >= aEnd;
}
