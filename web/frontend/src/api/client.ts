// One place that talks to the backend. SEC-02: the scope travels as a Bearer JWT
// issued by /api/auth/token — never as a query parameter — exactly like the
// legacy UI (web/static/app.js). Tokens are cached per scope for the page's life.
//
// Reads send the token too when a scope is given: the server serves a run at the
// caller's scope, and an investor must get the decision gate's withholding
// (validation/gate.py). With no scope, the server falls back to the stored scope.

import type { DecisionInput, Excerpt, Flag, Gate, Run, Scope, SelfReport, Session, Snippet } from "./types";

const tokens = new Map<Scope, string>();

async function token(scope: Scope): Promise<string> {
  const cached = tokens.get(scope);
  if (cached) return cached;
  const res = await fetch("/api/auth/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
  if (!res.ok) throw new Error(`Could not get a ${scope} token (HTTP ${res.status})`);
  const { access_token } = (await res.json()) as { access_token: string };
  tokens.set(scope, access_token);
  return access_token;
}

/** The Bearer header for `scope` — shared with the live stream (stream.ts). */
export async function authHeader(scope: Scope): Promise<Record<string, string>> {
  return { Authorization: `Bearer ${await token(scope)}` };
}

async function getJson<T>(url: string, scope?: Scope): Promise<T> {
  const res = scope
    ? await fetch(url, { headers: { Authorization: `Bearer ${await token(scope)}` } })
    : await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} failed (HTTP ${res.status})`);
  return (await res.json()) as T;
}

async function postJson<T>(url: string, scope: Scope, body: unknown, what: string): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${await token(scope)}` },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    // The server explains a refused decision in words (422 detail); show those.
    let detail = "";
    try { detail = ((await res.json()) as { detail?: unknown }).detail as string ?? ""; } catch { /* not JSON */ }
    throw new Error(`${what} (HTTP ${res.status})${typeof detail === "string" && detail ? `: ${detail}` : ""}`);
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

/** Fetch at `scope` and save the response as a file (the review export needs the Bearer token). */
async function download(url: string, scope: Scope, fallbackName: string): Promise<void> {
  const res = await fetch(url, { headers: { Authorization: `Bearer ${await token(scope)}` } });
  if (!res.ok) throw new Error(`Download failed (HTTP ${res.status})`);
  const name = res.headers.get("content-disposition")?.match(/filename="([^"]+)"/)?.[1] ?? fallbackName;
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(href);
}

export interface Config {
  provider: string;
  model: string;
  temperature: number;
  seed: number;
  agent_id: string;
  confidence_score: number;
  consistency_probe: boolean;
}

export const api = {
  runs: (scope?: Scope) => getJson<Run[]>("/api/runs", scope),
  run: (id: string, scope?: Scope) => getJson<Run>(`/api/runs/${enc(id)}`, scope),
  sessions: (scope?: Scope) => getJson<Session[]>("/api/sessions", scope),
  contradictions: (scope?: Scope) => getJson<Run[]>("/api/runs/contradictions", scope),
  flags: (id: string) => getJson<Flag[]>(`/api/runs/${enc(id)}/flags`),
  excerpt: (q: { cik: string; accn: string; concept: string; end: string; start?: string | null }) => {
    const params = new URLSearchParams({ cik: q.cik, accn: q.accn, concept: q.concept, end: q.end });
    if (q.start) params.set("start", q.start);
    return getJson<Excerpt>(`/api/facts/excerpt?${params}`);
  },
  snippet: (runId: string, url: string) =>
    getJson<Snippet>(`/api/runs/${enc(runId)}/source-snippet?${new URLSearchParams({ url })}`),

  addFlag: (scope: Scope, runId: string, flagType: Flag["flag_type"], note: string) =>
    postJson<Flag>(`/api/runs/${enc(runId)}/flags`, scope,
                   { flag_type: flagType, reviewer_note: note || null }, "Flag not recorded"),

  addDecision: (scope: Scope, runId: string, decision: DecisionInput) =>
    postJson<Gate>(`/api/runs/${enc(runId)}/decisions`, scope, decision, "Decision not recorded"),

  // U9 — the export (B6), and the pages that used to live only in the classic UI.
  downloadReview: (runId: string, scope: Scope) =>
    download(`/api/runs/${enc(runId)}/export.md`, scope, `review-${runId.slice(0, 8)}.md`),
  downloadAudit: (runId: string, scope: Scope) =>
    download(`/api/runs/${enc(runId)}/audit`, scope, `audit-${runId.slice(0, 8)}.json`),
  config: () => getJson<Config>("/api/config"),
  async setConfig(patch: Partial<Config>): Promise<Config> {
    const res = await fetch("/api/config", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
    });
    if (!res.ok) throw new Error(`Setting not saved (HTTP ${res.status})`);
    return (await res.json()) as Config;
  },
  directive: () => getJson<{ version: string; text: string }>("/api/directive"),
  selfReport: () => getJson<SelfReport>("/api/self-report"),
};

/** Exposed for tests only. */
export const _resetTokens = () => tokens.clear();
