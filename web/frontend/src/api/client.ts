// One place that talks to the backend. SEC-02: the scope travels as a Bearer JWT
// issued by /api/auth/token — never as a query parameter — exactly like the
// legacy UI (web/static/app.js). Tokens are cached per scope for the page's life.
//
// Reads send the token too when a scope is given: the server serves a run at the
// caller's scope, and an investor must get the decision gate's withholding
// (validation/gate.py). With no scope, the server falls back to the stored scope.

import type { AnalysisRecord, AnalysisSummary, MeasurementsResponse, DecisionInput, Excerpt, Flag, Gate, LiveRunSummary, Run, Scope, SelfReport, Session, Snippet } from "./types";

const tokens = new Map<Scope, string>();

// An auditor token needs the server's admin key (web/auth.py). It is asked for once, only
// when the server refuses an auditor token without it, and kept for this tab's session only.
const ADMIN_KEY_STORAGE = "mycroft-admin-key";

function storedAdminKey(): string | null {
  try { return sessionStorage.getItem(ADMIN_KEY_STORAGE); } catch { return null; }
}
function rememberAdminKey(key: string | null): void {
  try {
    if (key) sessionStorage.setItem(ADMIN_KEY_STORAGE, key); else sessionStorage.removeItem(ADMIN_KEY_STORAGE);
  } catch { /* private window: asked again next time */ }
}

async function requestToken(scope: Scope, adminKey: string | null): Promise<Response> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (adminKey) headers["X-Admin-Key"] = adminKey;
  return fetch("/api/auth/token", { method: "POST", headers, body: JSON.stringify({ scope }) });
}

/**
 * How the app asks for the admin key: a function returning the key typed, or null if it was
 * declined. App.tsx registers an in-page dialog. window.prompt isn't used: embedded webviews
 * (the Claude desktop browser pane among them) refuse it, found 2026-10-26.
 */
export type AdminKeyRequester = () => Promise<string | null>;
let askForAdminKey: AdminKeyRequester | null = null;
export const setAdminKeyRequester = (fn: AdminKeyRequester | null) => { askForAdminKey = fn; };

const inFlight = new Map<Scope, Promise<string>>();

function token(scope: Scope): Promise<string> {
  const cached = tokens.get(scope);
  if (cached) return Promise.resolve(cached);
  // Several requests start together on page load; they share one token request and one question.
  let pending = inFlight.get(scope);
  if (!pending) {
    pending = fetchToken(scope).finally(() => inFlight.delete(scope));
    inFlight.set(scope, pending);
  }
  return pending;
}

async function fetchToken(scope: Scope): Promise<string> {
  let res = await requestToken(scope, scope === "auditor" ? storedAdminKey() : null);
  if (scope === "auditor" && res.status === 403) {
    rememberAdminKey(null);
    const key = askForAdminKey ? await askForAdminKey() : null;
    if (key) {
      res = await requestToken(scope, key);
      if (res.ok) rememberAdminKey(key);
    }
    if (!res.ok) {
      throw new Error(key
        ? "That admin key was not accepted. Check ACCOUNTABILITY_ADMIN_KEY on the server, or switch to Investor."
        : "Auditor access needs the server's admin key, and none was given. Enter it, or switch to Investor.");
    }
  }
  if (!res.ok) throw new Error(`Could not get a ${scope} token (HTTP ${res.status})`);
  const { access_token } = (await res.json()) as { access_token: string };
  tokens.set(scope, access_token);
  return access_token;
}

/**
 * fetch() with the Bearer token for `scope`. A token the server no longer accepts (it was
 * restarted, so its signing secret changed) is dropped and fetched once more.
 */
async function authedFetch(scope: Scope, url: string, init: RequestInit = {}): Promise<Response> {
  const send = async () => fetch(url, {
    ...init, headers: { ...(init.headers as Record<string, string> | undefined), Authorization: `Bearer ${await token(scope)}` },
  });
  const res = await send();
  if (res.status !== 401) return res;
  tokens.delete(scope);
  return send();
}

/** The Bearer header for `scope` — shared with the live stream (stream.ts). */
export async function authHeader(scope: Scope): Promise<Record<string, string>> {
  return { Authorization: `Bearer ${await token(scope)}` };
}

async function getJson<T>(url: string, scope?: Scope): Promise<T> {
  const res = scope ? await authedFetch(scope, url) : await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} failed (HTTP ${res.status})`);
  return (await res.json()) as T;
}

async function postJson<T>(url: string, scope: Scope, body: unknown, what: string): Promise<T> {
  const res = await authedFetch(scope, url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
  const res = await authedFetch(scope, url);
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
  analysisMeasurements: (scope: Scope) => getJson<MeasurementsResponse>("/api/analysis/measurements", scope),
  analyses: (scope: Scope) => getJson<AnalysisSummary[]>("/api/analysis", scope),
  analysis: (id: string, scope: Scope) => getJson<AnalysisRecord>(`/api/analysis/${enc(id)}`, scope),
  runs: (scope?: Scope) => getJson<Run[]>("/api/runs", scope),
  run: (id: string, scope?: Scope) => getJson<Run>(`/api/runs/${enc(id)}`, scope),
  liveRuns: () => getJson<LiveRunSummary[]>("/api/runs/live"),
  sessions: (scope?: Scope) => getJson<Session[]>("/api/sessions", scope),
  contradictions: (scope?: Scope) => getJson<Run[]>("/api/runs/contradictions", scope),
  // Reviewer flags are the auditor's: the route refuses any other scope.
  flags: (id: string) => getJson<Flag[]>(`/api/runs/${enc(id)}/flags`, "auditor"),
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
    const res = await authedFetch("auditor", "/api/config", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
    });
    if (!res.ok) throw new Error(`Setting not saved (HTTP ${res.status})`);
    return (await res.json()) as Config;
  },
  directive: () => getJson<{ version: string; text: string }>("/api/directive"),
  selfReport: () => getJson<SelfReport>("/api/self-report"),
};

/** Exposed for tests only. */
export const _resetTokens = () => { tokens.clear(); rememberAdminKey(null); };
