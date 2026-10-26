---
title: Web server
slug: files-web
section: Files
order: 80
summary: The FastAPI server, its JWT scopes, the SQLite store, the step trace and the Honest Ledger.
---

# Web server

`web/` is the outermost layer of the subsystem. It is where a person, or the React review app,
reaches everything else: it runs one agent (`/api/chat`) or two (`/api/compare`), stores every run
in an append-only SQLite file, serves stored runs back through the decision gate at the reader's
scope, and publishes the subsystem's own list of what is broken.

The six files split the work like this:

| File | What it holds |
|---|---|
| [[web/server.py]] | The FastAPI app: every HTTP route, the in-memory run config, the chat and compare flows, the live event streams, and how a stored run is served at a scope. |
| [[web/auth.py]] | Issues and checks the bearer tokens that carry a scope (`auditor` or `investor`). |
| [[web/db.py]] | The SQLite store: tables, append-only triggers, the 90-day purge, one table migration. |
| [[web/step_trace.py]] | The ordered, timed record of what happened during one run, and the adapter wrappers that feed it. |
| [[web/self_report.py]] | The Honest Ledger: recorded live-model test results, known issues with their sources, and a live count of the automated tests. |
| [[web/__init__.py]] | Marks `web` as a package. |

The React app under `web/frontend/` is documented on its own page. The only part of it that
belongs here is that [[web/server.py]] serves its build (`web/frontend/dist/`) at `/app`, and
redirects `/` there.

**Dependency rule.** `web` may import from every other package (`core`, `adapters`, `pipeline`,
`datasources`, `producers`, `validation`); `tests/test_layering.py` holds that allow-list. One
dependency points the other way: [[validation/cross_validation.py]] persists a compare run through
`web.db`, and the layering test allows that only as a function-local import, so `validation/`
stays importable without `web/`.

**Two terms used throughout this page.**

- *Scope* is the reader tier from SEC-01. `auditor` sees the full record. `investor` never
  receives the internal tier (`thought_log`, `raw_output`, `llm_tokens`), and while a compare run
  is waiting for a human decision it also doesn't receive the disputed material.
- *The gate* is the human decision gate in [[validation/gate.py]]. A compare run whose agents
  disagree on a figure is `AWAITING_DECISION` until a named person records a decision for it.
  The run record itself never changes; its gate state is computed at read time from the run plus
  the `gate_decisions` rows.

**Deployment status.** The server is a localhost prototype. Most routes need no token, anyone can
mint an auditor token, and one unauthenticated route drops every table. The Honest Ledger records
this as [audit-criticals](ledger.html#audit-criticals) (critical, open).

## `web/__init__.py`

**Role:** empty package marker, so `uvicorn web.server:app` and imports such as `from web.db import
insert_run` resolve.

The file has no content. Nothing in the package relies on package-level state.

Related: [[web/server.py]]

## `web/auth.py`

**Role:** issues HS256-signed JWTs carrying a scope claim, and provides the two FastAPI
dependencies routes use to read that scope.

### What a token is

`issue_token(scope)` signs a payload with four claims:

| Claim | Value |
|---|---|
| `sub` | always the constant `"accountability-layer-user"` |
| `scope` | `"auditor"` or `"investor"` |
| `iat` | issue time (UTC) |
| `exp` | issue time plus `_TTL_HOURS` (8 hours) |

The token names a tier, not a person. `sub` is the same for every token, so no route can tell
two callers apart. That is why the decision gate records a typed name instead of an identity
([gate-identity-self-declared](ledger.html#gate-identity-self-declared)).

The signing secret is `ACCOUNTABILITY_SECRET` when it is set. When it is unset the module makes
a random one per process (`secrets.token_urlsafe(32)`), so no token can be forged from the source
code; the consequence is that tokens stop working when the server restarts, and the review app
fetches a new one (see [[web/frontend/src/api/client.ts]]). `_DEV_SECRET`, the old public
fallback, is used only when `ACCOUNTABILITY_INSECURE_DEV=1`, which also reopens everything below
for a throwaway local session. Until 2026-10-26 the public constant was the fallback, so anyone
who had read this file could sign a valid token without calling the server.

### Who can get a token

`POST /api/auth/token` in [[web/server.py]] calls `authorize_issue(scope, key)` first, then
`issue_token`. An investor token is open to anyone: it is the most restricted scope. An auditor
token needs the admin key, `ACCOUNTABILITY_ADMIN_KEY`, in an `X-Admin-Key` header, compared with
`hmac.compare_digest`. With no admin key configured the answer is 403 saying how to set one,
never an open door; a wrong or empty key is 403. `scripts/start-server.sh` generates a key into
`.env` the first time it runs and prints only where it saved it. The admin key is a shared
secret, not an identity: whoever holds it is "the auditor", and `sub` is still the same constant.

### Key functions

- `require_scope(credentials)`: the dependency for routes that must have a token. It reads
  `Authorization: Bearer <token>` through FastAPI's `HTTPBearer(auto_error=False)`, so a missing
  header reaches this function instead of being rejected by FastAPI with its own message.
  - No header: 401, "Missing Authorization header", with `WWW-Authenticate: Bearer`.
  - Expired token: 401, telling the caller to request a new one from `POST /api/auth/token`.
  - Bad signature or malformed token: 401, "Invalid token: " followed by the PyJWT error text.
  - Valid token whose `scope` claim is neither `auditor` nor `investor`: 403.
  - Otherwise it returns the scope string.
- `require_auditor(scope)`: `require_scope` plus a 403 for an investor token. It guards the routes
  that change settings, spend a model call or drop data.
- `optional_scope(credentials)`: the dependency for read routes. No header yields `None`; a header
  that is sent goes through `require_scope`, so a bad token is still refused. The caller decides
  what `None` means. [[web/server.py]] serves a token-less read at the investor scope (see
  `_read_scope` there); it used to serve the scope the run was stored under.
- `authorize_issue(scope, provided_key)`, `check_admin_key(provided)` and `admin_key()`: the rules
  above. `check_admin_key` also guards `/mcp` (a Bearer token carrying the admin key).

### What is and isn't authenticated

[[tests/test_auth_matrix.py]] walks every route on the app and states, for each, what an
unauthenticated caller, an investor token and an auditor token get; a route added without a row
fails the suite. In short (the route-by-route table is in the [[web/server.py]] entry):

- **Any token** (`require_scope`): `/api/chat`, `/api/chat/stream`, `/api/compare`,
  `/api/compare/stream`, `/api/analysis/stream`, `GET /api/analysis` and `GET /api/analysis/{id}`.
- **Auditor only** (`require_auditor`, or a 403 in the route): `POST .../decisions`,
  `POST` and `GET .../flags`, `POST .../replay`, `POST /api/config`, `DELETE /api/runs`. The last
  also needs `ACCOUNTABILITY_ALLOW_CLEAR=1` on the server.
- **Optional** (`optional_scope`, investor when absent): the run, session, audit, export,
  decision and snippet reads.
- **The admin key as a Bearer token:** `/mcp`, through a middleware in [[web/server.py]].
- **Public on purpose:** `GET /api/config`, `/api/directive`, `/api/self-report`,
  `/api/runs/live`, `/api/runs/drift`, `/api/facts/excerpt`, `/`.

### Limits and open issues

- [audit-criticals](ledger.html#audit-criticals) (critical, open): the four CRITICAL findings from
  the original audit. Three are addressed in code as of 2026-10-26 (auditor tokens need the admin
  key, the write and spend routes need a token, `DELETE /api/runs` is off by default). Built and
  tested, not yet observed on a restarted server. The fourth stays open: a run is stored at full
  detail and redacted when read, so anything that reads the database bypasses it.
- The admin key is one shared secret held in `.env` and, in the review app, in the tab's
  `sessionStorage` after it is typed once. There are no per-person credentials.
- [gate-identity-self-declared](ledger.html#gate-identity-self-declared) (medium, open): a token
  carries a scope, not a person.
- There is no revocation. A token is valid until its `exp`, or until the signing secret changes
  (a configured `ACCOUNTABILITY_SECRET` changed, or, with none configured, the server restarted),
  which invalidates every token already issued.
- A 401 for a bad token includes the library's error text in `detail`. That is harmless for a
  localhost prototype. Whether it is acceptable for a deployed service hasn't been considered
  (judgment, not recorded).

Related: [[web/server.py]], [[validation/gate.py]], [[tests/test_auth_matrix.py]]

## `web/db.py`

**Role:** the SQLite store (C-03). Runs and gate decisions are append-only, enforced by triggers.
It also holds the 90-day retention purge, one in-place table migration, and the read helpers the
routes use.

### The database file and connections

The database is `web/data/accountability.db` (`DB_PATH`), created on first use along with its
folder. Each helper opens its own connection through `_connect()`, which sets `row_factory` to
`sqlite3.Row` and enables `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`. The helpers use
the connection as a context manager, which commits or rolls back. It does not close the
connection; that is left to garbage collection.

`init_db()` runs the whole schema script, which is all `IF NOT EXISTS` and safe to repeat, and then
the gate-decisions migration. The server calls it at startup.
[[validation/cross_validation.py]]'s `persist_cross_agent_run` also calls it before every write,
so every compare persist re-checks the schema.

### Tables

**`runs`**: one row per run, chat or compare. The whole payload is one JSON blob. A few fields are
copied out into columns so they can be indexed.

| Column | Type | Filled from |
|---|---|---|
| `run_id` | TEXT, primary key | `payload["run_id"]` |
| `ticker` | TEXT NOT NULL | `_payload_ticker(payload)`: the payload's own `ticker` if it has one (compare and, since 2026-10-06, chat runs), else the company `datasources.tickers.ticker_for` finds in the subject, else `"CHAT"` |
| `scope` | TEXT NOT NULL | `payload["scope"]`, default `"auditor"` |
| `status` | TEXT NOT NULL | `"HALTED"` if `payload["halted"]`, else `"COMPLETE"` |
| `initiated_at` | TEXT NOT NULL | `payload["session"]["initiated_at"]` |
| `completed_at` | TEXT | `payload["session"]["completed_at"]` |
| `confidence` | REAL | `payload["confidence_score"]` |
| `payload_json` | TEXT NOT NULL | `json.dumps(payload, default=str)` |
| `created_at` | TEXT NOT NULL | SQLite default, UTC to the second |

There are indexes on `ticker`, `initiated_at` and `created_at`. The triggers `runs_no_update` and
`runs_no_delete` abort any `UPDATE` or `DELETE`, so the database engine refuses to change history,
whatever the application code does.

For a ticker-mode compare run, the `ticker` column is the ticker asked for. For a chat run or a
generic compare run it is the company the question names, found by [[datasources/tickers.py]]
("What was Apples revenue last quarter?" is `AAPL`), or `CHAT` if it names none. Until
2026-10-06 it was the first word of the message or subject, so a question beginning "What
year..." was stored under `WHAT`. Rows stored before then keep that value; history isn't
rewritten. `GET /api/runs?ticker=` and `/api/runs/drift` filter on this column.

`initiated_at` is NOT NULL and comes only from the nested session. That is why the chat route
always builds a session, even when the model call fails before producing one. See the 2026-09-22
RUN_LOG entry "Fix: /api/chat 500s on any adapter failure (missing session fallback)": before
that fix, any adapter error turned into a NOT NULL `IntegrityError` and a 500.

**`sessions`**: the serialised `RunSession` for a run.

| Column | Type | Notes |
|---|---|---|
| `session_id` | TEXT, primary key | always equal to the run id |
| `run_id` | TEXT NOT NULL | foreign key to `runs` |
| `ticker` | TEXT NOT NULL | upper-cased by `insert_session` |
| `session_json` | TEXT NOT NULL | the session dict as JSON |
| `created_at` | TEXT NOT NULL | SQLite default |

It is indexed on `ticker`. It has no append-only triggers, and `insert_session` writes with
`INSERT OR REPLACE`, so a second write for the same run id replaces the first.

**`reviewer_flags`** (UN-05): reviewer flags on a run, kept apart so the run row is never
mutated.

| Column | Type | Notes |
|---|---|---|
| `flag_id` | TEXT, primary key | a UUID4 |
| `run_id` | TEXT NOT NULL | foreign key to `runs`, indexed |
| `flag_type` | TEXT NOT NULL | CHECK: `Hallucinated`, `Incorrect` or `Other` |
| `reviewer_note` | TEXT | optional |
| `flagged_at` | TEXT NOT NULL | UTC to the second, set by `insert_flag` |

It has no append-only triggers either, but no code path updates or deletes a flag except
`clear_all` and the purge.

**`gate_decisions`** (BG): the human decisions that clear a gated compare run. It is append-only
like `runs`: `gate_decisions_no_update` and `gate_decisions_no_delete` abort any change. A later
decision on the same figure supersedes an earlier one, and both rows stay (the superseding logic
is in [[validation/gate.py]]).

| Column | Type | Constraint |
|---|---|---|
| `decision_id` | TEXT, primary key | a UUID4 |
| `run_id` | TEXT NOT NULL | foreign key to `runs`, indexed |
| `decided_by` | TEXT NOT NULL | CHECK: at least 2 characters after trimming |
| `decision` | TEXT NOT NULL | CHECK: one of `accept_a`, `accept_b`, `both_wrong`, `not_a_conflict`, `override_value`, `confirmed_error`, `set_grade` |
| `final_value` | REAL | optional |
| `final_grade` | TEXT | optional |
| `rationale` | TEXT NOT NULL | CHECK: at least 20 characters after trimming |
| `cited_items` | TEXT NOT NULL | a JSON list, decoded again on read |
| `decided_at` | TEXT NOT NULL | UTC with microseconds, set by `insert_decision` |

The CHECKs repeat the minimums that `validate_decision` in [[validation/gate.py]] enforces. The
schema comment gives the reason: a write that skips the route's validation still can't store an
empty decision. `tests/test_gate.py` covers that directly
(`test_database_refuses_an_empty_rationale_even_past_the_route`). `decided_at` keeps microseconds
so that two decisions in the same second still order correctly (code comment in
`insert_decision`). Reads order by `rowid`, which is insertion order: the order `gate_state()`
replays decisions in.

### Key functions

- `insert_run(payload)`: appends one run, filling the columns as in the table above.
- `get_runs(ticker=None, from_dt=None, to_dt=None, limit=50)`: the C-04 query. Filters on
  `ticker` (upper-cased) and on `initiated_at` as a string comparison, newest `created_at` first.
  It returns decoded payloads.
- `get_run(run_id)`: one decoded payload, or `None`.
- `get_drift(ticker)`: `run_id`, `initiated_at`, `confidence` and `status` for every run under
  that ticker, oldest first. Just the columns, not the payload.
- `insert_session`, `get_sessions(limit=50)`, `get_session(session_id)`: the session store.
- `insert_flag`, `get_flags(run_id)`: reviewer flags. Flags are returned newest first.
- `insert_decision(run_id, fields)`: appends one decision. `fields` must already have passed
  `validate_decision`. It returns the stored row, including its id and time.
- `get_decisions(run_id)` and `get_decisions_for(run_ids)`: decisions oldest first. The second
  answers many runs in one query for the History list, with an empty list for a run that has
  none.
- `clear_all()`: drops all five table names (including a leftover migration table) and recreates
  the schema. Its docstring says admin or test use only. It gets past the append-only triggers by
  dropping tables, not deleting rows, and it is what `DELETE /api/runs` calls.
- `migrate_from_json(runs, sessions)`: one-time import of the pre-SQLite JSON store. It skips runs
  and sessions already present, and returns how many runs it inserted.

### The financial agent's tables (2026-10-06)

Three append-only tables for [[financial_agent/PLAN.md]], each with no-update and no-delete
triggers:

- **`analysis_bundles`**, keyed by `bundle_hash` (CHECK: starts with `sha256:`), with `ticker`,
  `as_of` and the bundle JSON.
- **`analysis_runs`**, keyed by `run_id`, with `parent_run_id`, `ticker`, `bundle_hash`
  (foreign key), `agent_kind` and `status`. Both are CHECKed against [[core/analysis.py]]'s
  vocabularies.
- **`analysis_verdicts`**, with `run_id` (foreign key) and `verdict` (CHECKed against the five
  verdicts).

The functions:

- `insert_bundle(bundle)` returns `False` when the hash is already stored. It uses
  `ON CONFLICT(bundle_hash) DO NOTHING`, not `INSERT OR IGNORE`, because `OR IGNORE` also skips
  CHECK violations: a malformed hash went in silently until the test caught it.
- `get_bundle`, `insert_analysis(record)`, `get_analysis`, and
  `list_analyses(limit=50, ticker=None)`, newest first, as summaries without tool results.
- `insert_verdict(verdict)` fills in the id and time. `get_verdicts(run_id)` returns them oldest
  first, so the last one is current.
- `purge_old_analyses(retention_days=RETENTION_DAYS)` applies the same 90-day TTL. It removes
  expired runs with their verdicts, then bundles no remaining run references, and puts every
  no-delete trigger back in a `finally`. [[web/server.py]] calls it at startup, after
  `purge_old_runs`.

`clear_all` drops these three tables too. The rows hold JSON exactly as given: whether a bundle
is intact is checked by whoever reads it ([[financial_agent/replay.py]]), not trusted because it
came from this store. [[tests/test_analysis_record.py]] covers all of it.

### The retention purge

`purge_old_runs(retention_days=RETENTION_DAYS)`, where `RETENTION_DAYS = 90`, deletes runs whose
`created_at` (the time the row was written, not the run's `initiated_at`) is older than the cutoff.
It deletes the children first, because of the foreign keys: that run's gate decisions, flags and
sessions, then the run.

Triggers in SQLite belong to the schema, not to a connection, so the purge drops
`gate_decisions_no_delete` and `runs_no_delete` for the whole database, deletes, commits, and then
re-runs the schema script to recreate them. If anything raises, it rolls back and still re-runs
the schema script, so a no-delete trigger is never left dropped. The reason is in the code comment:
"never leave a no-delete trigger dropped". It returns the number of runs deleted.

`tests/test_gate.py`'s `test_ttl_purge_removes_decisions_with_their_run_and_restores_the_guard`
purges with `retention_days=-1`, then checks that the decisions went with their run and that the
no-delete guard is back.

### The gate-decisions migration

`_migrate_gate_decisions(conn)` runs from `init_db`. SQLite can't change a CHECK constraint in
place. The table was first created (BG, 2026-09-25) with five decision values; B3 added
`confirmed_error` the same day, and B5 added `set_grade` and the `final_grade` column. A table
whose stored SQL lacks `set_grade` or `final_grade` is rebuilt:

1. Drop the two triggers and the index, and rename the table to `gate_decisions_pre_b3`.
2. Run the schema script, which creates the new table, triggers and index.
3. Copy every row across in `rowid` order. `final_grade` is NULL for every copied row.
4. Compare row counts. If they differ, raise `RuntimeError` and leave the old table in place.
5. Drop the old table.

The docstring gives the guarantee: no decision is changed or lost. A table that already has both
markers is left alone, so the migration runs once. `tests/test_constraints.py` (a pre-B3 table is
migrated without losing a row) and `tests/test_synthesis.py`
(`test_a_b3_era_table_is_rebuilt_with_final_grade`) cover it. The leftover table keeps the name
`_pre_b3` for the B5 rebuild too.

### Design notes

- One blob per run, with a few columns for indexing, is the SDD §7.3 trade-off recorded in
  `docs/SYSTEM_DESIGN.md` §2.7. The comparison lives inside the blob, so "every contradiction"
  is a Python-side scan (`list_contradictions`), not a SQL `WHERE` clause.
- Gate state is computed, not stored. The module docstring says so: a run's gate state comes from
  its row plus its decisions at read time, so the run record itself never changes.

### Limits and open issues

- **Code and docs disagree on when the purge runs.** The module docstring says `purge_old_runs()`
  is "called automatically on startup and on every write", and `docs/SYSTEM_DESIGN.md` §2.7 says
  the same. The code calls it only from the server's startup handler; no insert helper calls it.
- **Code and docstring disagree on the ticker column.** The module docstring calls `ticker` "a
  generated column (extracted at insert time)". It is an ordinary column filled by Python
  (`_payload_ticker`), not a SQLite generated column.
- **The purge's comments overstate how local the bypass is.** Its docstring says it uses "a
  separate connection with the trigger temporarily disabled", and a comment says "for this
  connection only". The code drops the triggers from the database schema, so for the length of the
  purge any connection could delete rows. Its first statement drops a trigger named
  `runs_no_delete_ttl_bypass`, which the schema never creates, so that statement does nothing.
- `sessions` isn't append-only, and `insert_session` replaces rows.
- `get_runs`' `limit` is passed straight to SQLite. The route caps it at 200 but sets no lower
  bound, and SQLite reads a negative `LIMIT` as no limit.
- `migrate_from_json` inserts sessions with a plain `INSERT`. A legacy session whose run isn't in
  the file would fail the foreign key and abort the migration; the startup handler swallows that
  error silently (see [[web/server.py]]).
- Storage keeps the full record at every scope. Investor withholding happens only when a run is
  read ([session-scope-leak](ledger.html#session-scope-leak), open).

Related: [[web/server.py]], [[validation/gate.py]], [[validation/cross_validation.py]]

## `web/agent_sessions.py`

**Role:** an agent session (Claude Code, Codex, Cursor or any MCP client) standing in for a
model, so the whole pipeline runs unchanged when the agent is someone else's session.

- **`SessionAgent`** satisfies the same contracts a model does ([[core/contracts.py]]):
  - `adapter(subject, context, directive)` is one attempt of the slot's answer. It queues the
    prompt for the session, waits for the session's `submit`, and parses the text with
    [[core/parsing.py]]. A reply that breaks the output contract raises `StructuralParseError`,
    so the retry is the validation loop's own second call, with the reason attached to the next
    prompt (`previous_attempt_failed`).
  - `model_call(system, user)` is the assessment extraction, asked of the same session.
  - `finish(halted)` and `run_over(error)` release a waiting session when its side or the whole
    run ends.
- **The server parses, not the agent.** What the session submits is checked by the same
  parser as a model's reply.
- **Time limit:** `SESSION_TIMEOUT_S` (`AGENT_SESSION_TIMEOUT_S`, default 900 s). This is far longer
  than `MODEL_TIMEOUT_S`, because a person may be reading along. A silent session raises
  `SessionTimeout`, and the run is halted with "Agent session timed out".
- **Searches:** `record_search` runs a search on the session's behalf and records it in the trace,
  in the event shape the LangChain adapter's tool loop uses. The server observes it; it isn't the
  agent's self-report.
- **`start(run_id, agents, partner, work)`** runs the compare on a background thread and releases
  every session when it ends, even on an exception.

### Limits and open issues
- **The session's label is self-reported** (the MCP client's name, or `agent_label`); it is
  recorded, never verified ([session-identity-self-reported](ledger.html#session-identity-self-reported)).
- **The agent's own web searches aren't observed.** Only searches through the layer's `search`
  tool are recorded; sources found otherwise are known only through the citations, which are
  still fetched and checked.
- **Tested with this test suite standing in for the session**
  ([[tests/test_mcp_sessions.py]]). Not yet run with a real Claude Code, Codex or Cursor session.

Related: [[web/mcp_server.py]], [[web/server.py]], [[pipeline/middleware.py]]

## `web/live.py`

**Role:** the registry of runs in progress, so the review app can watch a run it didn't start.

- Every compare run registers while it runs (`register`), and every event the run produces is
  kept in order: `run_started`, the steps, `agent_finished`, then `result`.
- `wait_from(index, timeout)` returns the events from `index` on, waiting if there are none yet.
  [[web/server.py]]'s `GET /api/runs/{id}/live` uses it to replay a run and then follow it.
- `in_progress()` lists runs still running (`GET /api/runs/live`).
- A finished run stays for `FINISHED_TTL_S` (15 minutes) so a late watcher still sees the whole
  trace; after that, the stored record is the place to read it.
- It is thread-safe: runs execute on worker threads, and watchers read from the event loop.

### Limits and open issues
- **It lives in memory.** A server restart forgets runs in progress, but the stored records stay.
- **Full events are kept.** Investor filtering happens when they are served (no search text, no
  conclusions), the same rule as `/api/compare/stream`.

Related: [[web/server.py]], [[web/frontend/src/views/WatchView.tsx]]

## `web/mcp_server.py`

**Role:** the verification layer as an MCP server, mounted inside the web server at `/mcp`
(Streamable HTTP), so an agent you run yourself is the agent the layer checks, records and gates.

```bash
claude mcp add --transport http mycroft http://localhost:8000/mcp
```

| Tool | What it does |
|---|---|
| `start_compare(ticker \| subject, context, pairing, partner, agent_label)` | creates a run with this session as agent A; returns the directive, the subject and the lens's context. `partner` is `ollama` (the server runs agent B) or `session` (a second session joins) |
| `join_compare(run_id, agent_label)` | joins as agent B, once |
| `submit(run_id, slot, text)` | hands in a reply; returns `your_turn` (a retry, with the reason), `assessment_requested`, `accepted` or `halted`, and once the run is done its outcome |
| `search(run_id, slot, query)` | the layer's Tavily search, recorded in the trace; without `TAVILY_API_KEY` it says to use the agent's own search |
| `get_run(run_id)` | the run in progress (who is still working), or the outcome: figure rows, failed checks, gate status |

- **The server's instructions** (`INSTRUCTIONS`, shown to the agent by the client) explain the
  workflow. They tell the agent to cite every source as `[SOURCE: label, url]` and not to
  fabricate figures.
- **Every reply carries a `watch` link** (`MYCROFT_APP_URL` + `/app/#/live/{id}`).
- **No circular imports:** [[web/server.py]] passes in its compare runner and readers, so this
  module doesn't import it.

### Design notes
- **Why MCP.** The human has no API key, so the model runs in their own session. Keeping the
  layer on the other side of a tool interface means the layer never calls the model and never
  holds the user's credentials (`logs/RUN_LOG.md`, 2026-10-05, "An agent session as the agent,
  over MCP").
- **Why the official SDK.** The human chose it over a hand-written endpoint (same entry). It is
  a deliberate dependency exception (`requirements.txt`), optional at runtime.

### Limits and open issues
- **The endpoint is unauthenticated,** like the rest of the server
  ([audit-criticals](ledger.html#audit-criticals)), so keep it on localhost.
- **A session's identity is self-reported**
  ([session-identity-self-reported](ledger.html#session-identity-self-reported)).
- **Agent B's label** for `partner="session"` is fixed as "second session" when the run starts,
  because the trace labels are set before B joins.
- **Waits:**
  - `submit` waits up to 110 s for the next step;
  - `start_compare` and `join_compare` wait up to 120 s for the first prompt;
  - a longer wait returns `waiting`, and the agent calls `get_run` or `submit` again.

Related: [[web/agent_sessions.py]], [[web/live.py]], [[tests/test_mcp_sessions.py]]

## `web/self_report.py`

**Role:** the Honest Ledger. It holds the subsystem's own record of what has been tested, what
live models actually did, and what is broken, each item with its source. It also counts the
automated test suite live. `GET /api/self-report` serves it, and the reference site's ledger page
is generated from it.

### Why it exists

The module docstring says the UI must show what has actually been tested and what is actually
going wrong. Writing that into the UI code would drift from the record, and there would be no way
to tell measured claims from assumed ones. So every item carries its date, its source document or
RUN_LOG entry, and a status. The docstring's rule for keeping it honest: when a limitation is
fixed, change its status and add the date, and never delete the entry (P7). A limitation that
silently disappears can't be told apart from one that was never found.

### Status vocabulary

| Status | Meaning (from the module's comment) |
|---|---|
| `OPEN` | known broken or unfinished, not fixed |
| `RESOLVED` | was broken and has since been fixed (date recorded) |
| `BY_DESIGN` | deliberately not built: a scope decision, not a defect |
| `UNVERIFIED` | can't currently be claimed either way; no measurement exists |

A `UNVERIFIED` item is not given an optimistic default. The docstring says so explicitly.

### Structure

- **`LIVE_MODEL_TESTS`**: the recorded live-model tests. Each has `n`, `name`, `purpose`,
  `expected`, `actual`, `verdict`, `outcome` (`gap_found`, `worked` or `inconclusive`), `status`
  and `resolved_note`. These are results from runs against real local models (qwen2.5:7b, and in
  test 6 also mistral-7b, via Ollama), not scripted tests.
- **`LIVE_MODEL_TESTS_CAVEATS`**: what those tests don't establish, stated because a list of
  results invites the reader to assume more coverage than exists (module comment). The caveats
  cover sample size, the single model behind tests 1 to 5, and number-formatting noise in the
  divergence scores.
- **`KNOWN_ISSUES`**: the ledger proper. Each entry has `id`, `severity` (`critical`, `high`,
  `medium`, `low` or `info`), `area`, `title`, `detail`, `status` and `source`. Dated `UPDATE` and
  `CORRECTION` paragraphs are appended to `detail` instead of the text being rewritten. The
  entries themselves are rendered on the generated ledger page and aren't repeated here.
- **`build_self_report()`** returns:
  - `automated_tests`: from `_discover_test_counts()`, below;
  - `live_model_tests`: `run_on`, `model`, `tests` and `caveats`;
  - `known_issues`: the list as it stands;
  - `counts`: `issues_total`, `issues_open` (entries that are `OPEN` or `UNVERIFIED`),
    `issues_critical` (severity `critical`) and `live_tests_gaps_found` (outcome `gap_found`);
  - `deployment_status`: fixed text, `PROTOTYPE`, "Localhost only. Not deployable".

### How the test count is found

`_discover_test_counts()` (which also restores `os.environ` to what it was before discovery, since importing the test modules runs module-level setup such as the admin key in `tests/support.py`) runs `unittest.TestLoader().discover()` over `tests/`, with the
subsystem folder as the top level. That imports the test modules but runs none of them. It walks
the suite and counts test cases per module, by the last part of each test class's module name. It
returns `modules` (name and count, sorted), `total`, `load_errors` and `error`. If discovery
itself raises, it returns the error with `total: None` and no modules. The docstring gives the
reason: an explicit error, never "a plausible-looking zero". Discovery runs again on every call:
nothing is cached.

Importing the test package inside the server process is only safe if importing it changes nothing.
`tests/test_synthesis.py`'s `TestTestPackageIsInert` checks that importing the tests changes no
production code.

### Limits and open issues

- **`load_errors` can't detect a failed import.** It looks for modules whose name starts with
  `_Failed`. When a test module fails to import, unittest puts a `_FailedTest` case in the suite
  whose class lives in `unittest.loader`, so the walk files it under the module name `loader`,
  counts it as one test, and `load_errors` stays empty. Checked for this page by running discovery
  over a folder holding one module with a bad import: the case came back as module
  `unittest.loader`, class `_FailedTest`. The effect is that a broken test module shows up as a
  single test in a module called `loader`, not as a load error.
- **The comments count the live tests wrong.** The section comment says "The five live model tests
  (2026-08-29...)" and the caveats comment says "five green-ish rows". `LIVE_MODEL_TESTS` holds six:
  test 6 was added on 2026-09-11.
- **A test and the issue behind it disagree.** Live test 5 (multi-ticker breadth) is still `OPEN`,
  while the issue it produced, `disjoint-concepts`, is `RESOLVED`, and test 5's own `resolved_note`
  begins "Addressed 2026-09-11". The two records give different answers on whether the finding is
  closed.
- **A cited source is missing from the working tree.** `audit-criticals` cites
  `accountability-layer-audit.md` §2, and the module docstring lists that file as a source. The
  file is deleted in the working tree but not in the last commit. The 2026-09-27 RUN_LOG entry
  "Committing the work since c53746a, and a checkout-only test failure" records leaving that
  deletion out of the commits, because it wasn't part of that work and earlier entries say the
  file stays.
- The docstring's list of sources ends at the 2026-08-29 documents. Entries added since cite
  later RUN_LOG entries in their own `source` fields, which is where the provenance actually is.
- The docstring still says the alternative would have been hard-coding the claims "into app.js",
  which was the classic UI. That UI is archived (2026-09-27); the reasoning applies unchanged to the
  React app.
- `live_model_tests.run_on`, `model` and `deployment_status` are hand-written strings, not
  derived. `deployment_status` says the branch "is not merged"; nothing checks that.

Related: [[web/server.py]]

## `web/server.py`

**Role:** the FastAPI app. It holds every HTTP route, the in-memory run config, the chat and
compare flows with their live event streams, persistence of each run, and the rules for which
scope a stored run is served at.

Start it from the `verification-layer/` folder with `uvicorn web.server:app --port 8000` (the
module docstring adds `--reload`). The API's interactive docs are at `/docs`. The generated route
table on this site lists every route. This entry explains them.

### Startup and module-level state

- **`.env` loading.** At import, before the other imports, `load_dotenv(find_dotenv(usecwd=True)
  or find_dotenv())` searches upward from the working directory for a `.env`. Variables that
  modules read at import time, such as `LANGCHAIN_MODEL`, are therefore in place first.
- **The `/app` mount.** If `web/frontend/dist/` exists at import time, it is mounted at `/app`
  through `_FrontendFiles`, a `StaticFiles` subclass with `html=True`. It adds
  `Cache-Control: no-cache` to every HTML response. Its docstring gives the reason: `index.html`
  names the current build's content-hashed assets, and a heuristically cached copy kept pointing a
  browser at the previous build (found while verifying U1). The hashed assets can be cached
  normally.
- **The classic UI** once lived in `web/static/` and was archived on 2026-09-27 (U9 cutover) to
  `archive/web-static-legacy/`. Its `/static` mount went with it. `STATIC_DIR` is kept only so a
  restore is a two-line change (code comment).
- **The startup handler** calls `init_db()` and `purge_old_runs()` (see [[web/db.py]]). If the
  legacy JSON store `web/data/runs.json` exists, it runs `migrate_from_json`. If anything was
  imported, it renames the file to `runs.json.migrated` instead of deleting it, "so the file isn't
  lost". Any exception in that migration is swallowed ("corrupt legacy file — ignore").
- **`_BACKGROUND_RUNS`** holds a reference to each streamed run's task until it finishes, because
  a task nothing refers to can be garbage-collected mid-run (code comment).

### Run config (`_config`)

`_config` is one module-level dict, held in memory and shared by every request.

| Key | Default | Meaning |
|---|---|---|
| `provider` | `"langchain"` | fixed; kept only because [[adapters/registry.py]] reads it. LangChain is the only agent framework. |
| `model` | `LANGCHAIN_MODEL`, else `"llama3.2"` | the model name. [[adapters/langchain_adapter.py]] infers Ollama or Gemini from the name. |
| `temperature` | `0.0` | deterministic by default (code comment) |
| `seed` | `42` | stored with each run for replay |
| `agent_id` | `"external"` | the chat run's `AgentID` |
| `confidence_score` | `0.75` | the chat run's starting confidence |
| `consistency_probe` | `False` | opt-in for the ADR-06 probe (but see the chat flow: it is on anyway for any non-Gemini model) |

`GET /api/config` returns it. `POST /api/config` takes a `ConfigUpdate`, where every field is
optional, and applies only the fields sent:

- `temperature` is rounded to 2 places;
- `confidence_score` is clamped to 0 to 1, then rounded;
- `agent_id` must be a valid `AgentID`, or the route returns 400 "Unknown agent_id";
- `model`, `seed` and `consistency_probe` are stored as given.

It returns the new config. There is no `provider` field; the code comment says the model is still
settable because it is recorded per run as evidence. Changes last until the process restarts, and
apply at once to every later run from every caller.

### Scope on reads: `_read_scope` and `_serve_runs`

The read routes take `optional_scope` from [[web/auth.py]]. They serve a stored run at
`_read_scope(token_scope, run)`: the token's scope if a token was sent, else `"investor"`. Until
2026-10-26 a read with no token was served at the scope the run was stored under, so anyone
without a token could read, at auditor scope, any run an auditor created (the 2026-09-25 entry
"BG + U3: the human decision gate, backend and inline UI" recorded that as open). The review app
sends a token on every read that has a scope, so it is unaffected; a bare `curl` now gets the
investor view.

`_serve_runs(runs, token_scope)` fetches decisions in one query (`get_decisions_for`) for the runs
that carry a `gate_policy`, and passes each run through `redact_for_scope(run, read_scope,
gate_state(run, decisions))` from [[validation/gate.py]]. That call adds the `gate` block and
applies the investor rules: no internal tier, including in the nested session; and while the run
is `AWAITING_DECISION`, the disputed material and search text are withheld. Runs stored without
`gate_policy` predate the gate and are never gated retroactively.

### Routes

"Token" is one of three things: *required* (`require_scope`), *optional* (`optional_scope`, served
through `_read_scope`) or *none*. A request body that fails its Pydantic model returns FastAPI's
422.

| Method and path | Token | Request | Response and errors |
|---|---|---|---|
| `POST /api/auth/token` | none, but an auditor token needs `X-Admin-Key` | `TokenRequest` `{scope: "auditor"\|"investor"}` | `{access_token, token_type: "bearer", scope}`; 403 for an auditor token without a valid key, or when the server has no key configured. |
| `GET /` | none | none | 307 redirect to `/app/` if `web/frontend/dist/` exists, else a 503 HTML page saying how to build the UI. Both send `Cache-Control: no-store`. |
| `GET /api/config` | none | none | the `_config` dict |
| `POST /api/config` | required, auditor only | `ConfigUpdate` | the updated `_config`; 400 on an unknown `agent_id`; 401, 403 |
| `POST /api/chat` | required | `ChatRequest` `{message, context=""}` | the chat payload (below); 401 or 403 from auth |
| `POST /api/chat/stream` | required | `ChatRequest` | `text/event-stream` of the same run |
| `POST /api/compare` | required | `CompareRequest` | the compare payload (below) |
| `POST /api/compare/stream` | required | `CompareRequest` | `text/event-stream` of the same run |
| `GET /api/runs/contradictions` | optional | query `ticker`, `limit` (default 50, at most 200) | stored compare runs whose comparison flagged a contradiction, each served through `_serve_runs` |
| `GET /api/runs` | optional | query `ticker`, `from`, `to` (ISO-8601, compared with `initiated_at`), `limit` (default 50, at most 200) | stored runs, newest first, each served through `_serve_runs` |
| `GET /api/runs/drift` | none | query `ticker` (required) | `{ticker, points: [{run_id, initiated_at, confidence, status}]}`, oldest first |
| `GET /api/runs/{run_id}` | optional | none | one run through `_serve_runs`; 404 "Run not found" |
| `GET /api/facts/excerpt` | none | query `cik`, `accn`, `concept`, `end`, optional `start` | the filing excerpt from [[datasources/filings.py]]; 422 "Malformed parameter(s)" |
| `GET /api/runs/{run_id}/source-snippet` | optional | query `url`, optional `figure` | the recorded search snippet for that URL; 404 |
| `GET /api/runs/{run_id}/audit` | optional | none | the audit record as JSON ([[validation/audit.py]]); 404; 422 if not a compare run |
| `GET /api/runs/{run_id}/export.md` | optional | none | the audit record as Markdown, sent as a download; same errors |
| `GET /api/runs/{run_id}/decisions` | optional | none | the run's `gate` block after redaction; 404 |
| `POST /api/runs/{run_id}/decisions` | required, auditor only | `DecisionRequest` | the gate state after the decision is appended; 403 for investor; 404; 422 with the refusal reason |
| `POST /api/runs/{run_id}/replay` | required, auditor only | none | a determinism check (below); 404; 400; 500 "Replay failed" |
| `POST /api/runs/{run_id}/flags` | required, auditor only | `FlagRequest` `{flag_type, reviewer_note?}` | the stored flag; 403 for investor; 404 |
| `GET /api/runs/{run_id}/flags` | required, auditor only | none | the run's flags, newest first; 404; 401, 403 |
| `GET /api/sessions` | optional | none | the 50 newest sessions, each through `_serve_session` |
| `GET /api/sessions/{session_id}` | optional | none | one session through `_serve_session`; 404 "Session not found" |
| `DELETE /api/runs` | required, auditor only | none | `{cleared: true}` after `clear_all()` drops and recreates every table; 403 unless the server has `ACCOUNTABILITY_ALLOW_CLEAR=1` |
| `POST /api/analysis/stream` | required | `AnalysisRequest` `{message}` | `text/event-stream`: `run_started`, one `step` per step, then `result` with the analysis record (or `error`); 422 when no company is named, 503 when price data is unavailable |
| `GET /api/analysis` | required | query `limit` (1 to 200, default 50) | stored analyses, newest first, as summaries without tool results or replies |
| `GET /api/analysis/measurements` | required | none | `{model, reasoning, prompt_version, measurement}`: how the configured model did on the last format measurement, or `measurement: null` when it was never measured |
| `GET /api/analysis/{run_id}` | required | none | one analysis record; an investor token gets the thought log and raw replies withheld; 404 |
| `GET /api/directive` | none | none | `{version, text}` of the active directive |
| `GET /api/self-report` | none | none | `build_self_report()` from [[web/self_report.py]] |

`/api/runs/contradictions` and `/api/runs/drift` are declared before `/api/runs/{run_id}`, so
FastAPI matches them first and doesn't read `contradictions` or `drift` as a run id.

The model-running routes (`/api/chat`, `/api/compare`, replay) and most read routes are plain
`def`, so FastAPI runs them on its threadpool. The `/api/chat` docstring gives the reason: as
`async def` the blocking model call ran on the event loop and stalled every other request. That
was found and fixed in the 2026-09-24 entry "BL: unblock the event loop, run agents concurrently,
stream live events", which measured five `GET /api/runs` probes during a live MSFT compare
returning in 0.057 to 0.077 s. `POST` and `GET .../flags`, `GET /api/runs/drift`, `DELETE
/api/runs`, the config, directive and self-report routes are `async def` and call synchronous
code, so they briefly block the loop while they run.

### The chat flow (`_run_chat`)

`/api/chat` and `/api/chat/stream` both call `_run_chat(request, scope, emit=None)`. One agent
runs through the full accountability loop:

1. **Set-up.** A new run id; the agent id from `_config`; the active directive; a new
   `StepTrace`. When streaming, `emit` is subscribed to the trace and a `run_started` event is sent
   (`{run_id, mode: "chat", subject}`).
2. **Adapter.** The adapter is built from a per-call copy of `_config` that carries
   `_on_tool_event`, which turns each tool event from [[adapters/langchain_adapter.py]] into a
   trace step (phase `chat`). The copy exists so the callable never reaches the
   `config_snapshot` that is serialised into the record (code comment). The adapter is wrapped in
   `wrap_adapter`, so each LLM attempt becomes a timed step.
3. **Canonical input.** `message` and `context` are normalised by `_canonicalize`: Unicode NFC,
   trimmed, runs of spaces and tabs collapsed (newlines kept). The docstring gives the reason: the
   same logical input must give the same prompt bytes, a prerequisite for seed-based replay.
4. **Confidence.** Chat has no data source (`data_sources` is empty; no simulated stand-in is
   substituted). `_degrade_confidence` therefore leaves the configured score unchanged.
   `_classify` marks it `HIGH_UNCERTAINTY` below 0.4, otherwise `STANDARD`.
5. **The loop.** `run_validation_loop` from [[pipeline/middleware.py]] runs the ADR-07
   attempt-and-retry loop with the wrapped adapter.
6. **On success.** It builds a `COMPLETE` `RunSession`. The top-level `reasoning_objects` are
   serialised at the caller's scope. `conclusion` is set, and `thought_log` is set only for an
   auditor. Then the ADR-06 checks:
   - claims are extracted from the thought log and conclusion and checked against their cited
     sources ([[validation/claims.py]], [[validation/verification.py]]), filling `claims` and
     `verification_rate`. Since 2026-10-05, `_retrieved_urls` gives the verifier every URL the
     agent could have read: its search results from this run's trace, URLs written in the subject
     and Context, and fetched data sources. A citation to anything else is `not_retrieved`;
   - the consistency probe ([[validation/consistency.py]]) asks the same question again and
     scores the agreement into `consistency`. It runs when `_config["consistency_probe"]` is true
     *or* the model name doesn't contain "gemini", so it is on by default for every Ollama-family
     model. The code comment gives the reason: the Ollama determinism claim is documented as
     unreliable ([ollama-determinism](ledger.html#ollama-determinism)).
   - **Confidence after the checks** (since 2026-10-05). `_check_penalties` lowers the run's
     score, never below 0.05:
     - by 0.3 for a cited source the agent never retrieved, or a page that doesn't exist;
     - by 0.15 when citations were checked and none confirmed a figure;
     - by 0.2 when the probe's figures diverge, or 0.1 when its agreement is only LOW.

     A failed probe counts for nothing. The amounts are a judgement, stated as such in the code:
     two serious findings take 0.75 below 0.4. The payload, the session and the stored row carry
     the lowered score. `confidence_before_checks` and `confidence_adjustments` (each `{reason,
     penalty}`) record how it got there. The reasoning objects keep the score they were given.
7. **On failure.**
   - `HaltError` (two parse failures) gives a `HALTED` session with the failed attempts' reasoning
     objects, and the error text.
   - `EnvironmentError` gives "Configuration error: ...".
   - `LangchainConnectionError`, which includes the model-call timeout, gives "Model or search
     tool unreachable: ...".
   - Any other exception gives "Adapter error — `Type`: ...".

   Every failure path sets `halted: true`.
8. **Always.** If no session was built, a minimal `HALTED` session is created, so the row has an
   `initiated_at` (see [[web/db.py]]). `steps` is set from the trace whatever the outcome, so a
   failed run keeps what happened before the failure. The run is stored with `insert_run`, and
   its session with `insert_session`.

The chat payload has these keys: `run_id`, `subject` (the canonical message), `scope`, `halted`,
`conclusion`, `thought_log`, `confidence_score`, `confidence_degraded`,
`confidence_classification`, `high_uncertainty`, `data_sources`, `reasoning_objects`, `session`,
`error`, `config_snapshot` (a copy of `_config`), `steps`, `tool_capability_warning`, `claims`,
`consistency` and `verification_rate`, plus `confidence_before_checks` and
`confidence_adjustments` on a completed run. The context isn't stored as a field of its own.

`tool_capability_warning` is set only when search can't run (no `TAVILY_API_KEY`, so the
provider's `supports_tools()` is false) *and* the message or context contains a URL. The docstring
gives the reason: a user who hands the agent something to check shouldn't silently get an answer
from the model's training data. The docstring points to the RUN_LOG's "Mad Max" entry: the
2026-09-23 entry "Generalize the accountability layer + Cross-Agent Validation beyond finance
(superseded mid-implementation, kept)", which added `_tool_capability_warning()`.

### The compare flow (`_run_compare`)

`/api/compare` and `/api/compare/stream` both call `_run_compare(request, scope, emit=None)`. The
two agents are run through [[validation/cross_validation.py]]'s `run_cross_agent_validation`.

**The request (`CompareRequest`).** Exactly one of `ticker` or `subject` must be set; a validator
rejects both or neither, so the request fails with 422.

- `ticker` mode is the EDGAR-backed financial path. The two agents see different slices of one
  SEC filing payload.
- `subject` mode is generic. Both agents get the same subject and `context`. It was added after a
  live test asked two agents to check a film's release year and found no non-financial path (code
  comment; RUN_LOG 2026-09-23 "Generalize the accountability layer + Cross-Agent Validation beyond
  finance (superseded mid-implementation, kept)", where the question was whether Mad Max was made
  in 2015).
- `agent_a_model` and `agent_b_model` override the configured model for one side, through
  `with_model_override` in [[adapters/registry.py]].
- `contradiction_rule` overrides which rule sets `contradiction_flag`: `concept_aware`,
  `canonical_facts` or `symmetric_difference`.
- `pairing` applies to ticker mode only. `lenses` (the default) is financial against earnings;
  `bull_bear` gives both agents the same figures, argued both ways. The pairs come from
  `producers.PAIRINGS`.

**Ticker mode, step by step.**

1. The producer metadata is built: agent id, role (`PRODUCER A` and `PRODUCER B`, or `BULL` and
   `BEAR`), lens name, version, concept list, model label, whether it was overridden, the concepts
   both lenses share, and `same_model`. The concept lists come from the lens modules, so the UI
   never hard-codes them (code comment).
2. Shared steps, each recorded once in phase `shared`:
   - `lookup_cik` resolves the ticker to a CIK (kind `fetch`).
   - `fetch_company_facts` makes one companyfacts fetch (kind `fetch`), "one payload, reused by
     BOTH producers".
   - One summarise step per lens builds that agent's context string from the same payload. Step
     labels keep their pre-B4 names (`summarize_facts`, `summarize_earnings_facts`) so stored
     traces read the same (code comment).
3. `facts` holds each lens's structured figures. A concept missing from the filing is kept, as
   `{concept, missing: true}`.
4. Both agents get one `DataSource` (SEC EDGAR, `LIVE`, the companyfacts URL).
5. The default rule is `concept_aware` for `lenses` and `canonical_facts` for `bull_bear`.
   Reasons, from the code comments: `concept_aware` measured best on the real-run corpus; bull and
   bear see identical figures, so any figure both cite is comparable.
6. One extra model call per agent reads its finished answer for a grade and direction: the
   assessment extraction (B4, option 1), traced by `wrap_model_call`.

**Subject mode.** Both agents get the same context. A `shared context` note is recorded. No data
sources and no facts are attached. The default rule is `canonical_facts` with years included. The
code comment gives the reason: the old `symmetric_difference` rule couldn't see a bare year, so
"2010" against "2015" passed as no contradiction. There is no assessment extraction: there is no
company to grade.

**Then, in both modes.**

1. Each agent's adapter is built and wrapped in `wrap_adapter` (phases `agent_a` and `agent_b`),
   with its own tool-event recorder.
2. `run_cross_agent_validation` runs the two agents. By default they run at the same time, on two
   threads, which is why the trace is thread-safe. `CROSS_AGENT_MAX_CONCURRENCY=1` makes them run
   A then B. `reasoning_objects` stays in A-then-B order either way.
3. A `compare conclusions` note (phase `compare`, kind `compare`) records the comparison status
   and rule.
4. `halted` is true unless the comparison status is `COMPARED`. `cross_agent_comparison` is the
   result, and `reasoning_objects` is serialised at the caller's scope, not taken from what
   storage holds.
5. Claims are extracted and verified per agent, from that agent's own successful attempt only, so
   a claim from A's thought log is never checked against B's citations. For an investor the claim
   list is empty but the rate is kept. The code comment gives the reason: the claims are drawn
   from the thought log, which investors don't get. The comment also records why this step exists
   at all: before 2026-09-11 the AAPL 0.34 debt-to-equity fabrication was only ever caught by a
   person reading the raw thought log
   ([fabrication-not-caught](ledger.html#fabrication-not-caught)).
6. `persist_cross_agent_run` stores the run at the caller's scope, with the extra keys a record
   reopened later needs: `ticker`, `producers`, `contexts`, `facts`, `claims`,
   `verification_rate`, `steps` and `gate_policy`. The run is gateable because it carries
   `gate_policy`. The response gets `session`, `gate_policy` and `gate` (computed with no
   decisions yet).
7. The handlers catch `EdgarFetchError` ("EDGAR fetch failed: ..."), `EnvironmentError`,
   `LangchainConnectionError` and any other exception, as in chat, and set `halted`. These
   handlers run before `persist_cross_agent_run`, so a run that ends in one of them is returned
   but not stored. Whether an agent's own failure is caught per agent inside
   `run_cross_agent_validation` instead is that module's behaviour.
8. `steps` is set after the `try`, so a partial trace still shows where a failure happened (code
   comment).
9. An investor whose run is `AWAITING_DECISION` gets the payload through `redact_for_scope`, so
   the live response withholds what a later read would.

The compare payload has these keys: `run_id`, `ticker`, `subject`, `scope`, `halted`,
`cross_agent_comparison`, `reasoning_objects`, `session`, `error`, `claims` (`{a, b}`),
`verification_rate` (`{a, b}`), `producers`, `contexts` (the exact strings each agent was given),
`facts` (`{a, b}`), `steps`, `tool_capability_warning`, and, when the run completed, `gate_policy`
and `gate`.

### Live streams (`_event_stream`)

The two `/stream` routes run the same synchronous function as the plain routes on a worker thread
(`asyncio.to_thread`). `emit` hands each event to the event loop with `call_soon_threadsafe`, onto
an `asyncio.Queue`. The body turns each queued event into a server-sent event (SSE): an
`event: <name>` line and a `data: <json>` line. The response is `text/event-stream` with
`Cache-Control: no-cache` and `X-Accel-Buffering: no`.

| Event | When | Data |
|---|---|---|
| `run_started` | first | chat: `{run_id, mode, subject}`; compare: also `ticker` and `producers` |
| `step_started`, `step_finished` | as trace steps start and end | a copy of the step (see [[web/step_trace.py]]) |
| `agent_finished` | compare only, as each agent completes | `{agent, halted, conclusion}` |
| `result` | last, on success | the exact payload the plain route returns |
| `error` | instead of `result`, if the work function raises | `{error: "Type: message"}` |

**Ordering.** Every `emit` from the worker is scheduled before `asyncio.to_thread`'s own completion
callback, so `result` is always the last event before the stream closes (code comment).

**Disconnects.** A client disconnect ends the stream, not the run. The runner task keeps going and
stores the record exactly as the plain route would. The code comment gives the reason: "The
record is the evidence; a closed browser tab must not be able to delete it."

**Investor callers on `/api/compare/stream`.** Every step event passes through
`strip_search_content` first, and `agent_finished` carries `conclusion: null`. Whether the gate
will withhold the run isn't known until both agents are compared (code comments). This path was
closed on 2026-09-26; see the RUN_LOG entry "Correction: two claims in earlier entries were wrong;
the trace leak they hid is fixed", and
[trace-leaks-disputed-values](ledger.html#trace-leaks-disputed-values), resolved and observed on a
restarted server the same day. `/api/chat/stream` subscribes `emit` unfiltered at every scope.
Chat runs are never gated, so there is nothing for it to withhold.

**Tested with scripted agents:** `tests/test_concurrency_and_stream.py` checks both streams' event
sequences and that the streamed `result` has the same keys as `/api/compare`'s.
`tests/test_trace_withholding.py` checks that an investor stream carries no search text and that
its result is gated. **Observed live:** the 2026-09-24 BL entry records streamed AAPL and MSFT
compares on llama3.2 with real Tavily and EDGAR. Their `started_at` spans overlapped by 39.4 s and
16.9 s.

### Other routes in detail

- **`GET /api/facts/excerpt`** (BP). Every parameter ends up in an SEC URL or a cache file name,
  so each must match its exact format first (code comment):
  - `cik`: 1 to 10 digits;
  - `accn`: `##########-##-######`;
  - `concept`: a letter, then 1 to 120 letters or digits;
  - `end` and `start`: `YYYY-MM-DD`.

  It is lazy (nothing is fetched until someone asks), makes a network call to SEC, and needs no
  token. Its docstring says it never returns 500 on an odd filing: `status` says what happened
  (`found`, `not_found`, `no_inline_xbrl`, `too_large`, `fetch_failed` or `not_in_index`), and the
  filing index URL is returned either way.
- **`GET /api/runs/{run_id}/source-snippet`** (BP). It returns the search result the agent read
  for `url`, as recorded at the time; the page is not fetched again. With `figure`, it also returns
  `highlight`: a `[start, end]` span of that figure in the snippet. If the exact text isn't found,
  it tries again on the number alone, so "$94.9 billion" can match "94.9 billion". `agent` is `a`
  or `b`, from the step's phase. Runs recorded before per-result snippets were kept get
  `not_found` with a message saying so. An investor read of an `AWAITING_DECISION` run gets
  `status: "withheld"`; this is the scope check added on 2026-09-26.
- **`GET /api/runs/{run_id}/audit` and `/export.md`** (B6). `_audit_for` serves the run through
  `_serve_runs` first, and then builds `audit_record`. The code comment gives the reason: an
  investor's export carries no more than an investor's read. The Markdown is sent as
  `review-<first 8 characters of the id>-<scope>.md`. `tests/test_audit.py` checks that exporting
  changes nothing stored, and what an investor export contains.
- **`GET /api/runs/{run_id}/decisions`** returns only the `gate` block, after the same redaction
  as the run. The docstring gives the reason: a pending check's arithmetic states the disputed
  figure.
- **`POST /api/runs/{run_id}/decisions`** (P4). The scope is checked first (an investor gets
  403), then that the run exists (404). The body (`DecisionRequest`: `decision`, `decided_by`,
  `rationale`, `cited_items`, optional `final_value` and `final_grade`) is validated by
  `validate_decision`. A refusal becomes a 422 with the reason in words. `DecisionRequest` checks
  shape only, so the database CHECKs, the route and the tests share one definition of the rules
  (code comment). The decision is appended, never edited, and the route returns the new gate
  state. `decided_by` is whatever was typed
  ([gate-identity-self-declared](ledger.html#gate-identity-self-declared)).
- **`POST /api/runs/{run_id}/replay`** (Week 6 determinism). It rebuilds the adapter from the
  stored `config_snapshot` (400 if that fails), picks the directive version from the stored
  session (falling back to the active directive), and runs `run_validation_loop` again with a new
  run id and an empty context. The code comment gives the reason: "context not stored
  separately". It returns `original_conclusion`, `replay_conclusion`, `match` (byte-identical),
  `diff_chars` and `original_config`. A halt returns `replay_halted: true` with the error; any
  other exception returns 500. Nothing is stored.
- **Reviewer flags** (UN-05). `POST` is auditor-only and never changes the run itself. `GET` is
  auditor-only too, since a flag carries the reviewer's free-text note.
- **Sessions.** `_serve_session` looks up the session's run and works out the read scope. Auditor
  reads get the session unchanged. Investor reads get it through `redact_for_scope`. The code
  comment gives the reason: a session nests the run's reasoning objects, conclusions included.
- **`DELETE /api/runs`** drops and recreates every table, including the append-only decisions and
  flags. The React app deliberately has no button for it, because it contradicts the
  repository's never-delete rule. Since 2026-10-26 it needs an auditor token and
  `ACCOUNTABILITY_ALLOW_CLEAR=1` on the server, and answers 403 otherwise
  ([clear-all-runs-not-in-ui](ledger.html#clear-all-runs-not-in-ui), by design).
- **The analysis routes** (financial agent, 2026-10-26). `POST /api/analysis/stream` plans the
  request first (`fa_agent.plan_request`: 422 with what to change when no company is named,
  503 when `unavailable_reason` says price data can't be fetched), then streams a run in a worker
  thread like the compare streams: `_prepare_bundle` freezes the data, `fa_agent.run_analysis`
  runs the model through the retry-then-halt loop, and the bundle and record are appended
  (`insert_bundle`, `insert_analysis`). A model that can't be reached ends the stream with an
  `error` event and stores nothing. When the agent finishes, the route runs Tier 1
([[validation/analysis_checks.py]]) and stores the result in the record as `tier1`: the agent package
doesn't import `validation/`, so the route is where the two meet. `_serve_analysis` withholds the thought log and each attempt's
  raw reply from an investor token; what is stored is complete. Both token scopes may ask. The
  signal is a model's reading of the data and nothing here checks it yet.
- **The MCP guard.** `_guard_mcp`, an HTTP middleware, answers 401 to any `/mcp` request that
  doesn't carry the admin key as a Bearer token, unless `ACCOUNTABILITY_INSECURE_DEV=1`.
- **`GET /api/self-report`** is unauthenticated on purpose. Its docstring says it is the
  subsystem's own honest ledger, shown prominently rather than buried.

### Design notes

- **Redact at response time, not from storage.** Both model routes serialise `reasoning_objects`
  at the caller's scope. The SEC-01 comment above the compare routes gives the reason:
  `build_run_payload` in [[validation/cross_validation.py]] always writes the full auditor view,
  and fixing that meant changing shared code the change was scoped not to touch. The route "is
  exactly as secure as every other route today: not worse, not fixed." The 2026-08-28 RUN_LOG
  entry "Configure GEMINI_API_KEY; add queryable contradictions, model heterogeneity, and an HTTP
  route" verified this with a live smoke test at investor scope.
- **One fetch, drawn once.** The shared EDGAR fetch is traced once, in phase `shared`, so the UI
  can't draw it as two fetches (see [[web/step_trace.py]]).
- **Thin adapter aliases.** `_producer_config`, `_model_label`, `_build_adapter` and
  `_build_model_call` are aliases for [[adapters/registry.py]]. The comment explains that this
  module used to carry its own copies, which restated the provider set every time one was added.
- **Tool steps carry structured fields.** `_tool_step_extra` copies the adapter's tool event into
  `kind: "tool"`, `tool`, `tool_phase`, `query`, `args`, `retried_query_only`, `urls` and
  `results`. The UI never has to parse a label (docstring). The tool's phase is stored as
  `tool_phase` because step extras are merged over the step's own keys, and a `phase` key would
  overwrite the trace phase. `_tool_event_detail` puts the query first. Its docstring says the
  timeline used to show that a search happened but never what it was for.

### Test coverage

Route tests use FastAPI's `TestClient` with scripted agents (`tests/support.py`, never reachable
from the running app). No live model is called in any of them.

- `tests/test_compare_route.py` covers `/api/compare`.
- `tests/test_concurrency_and_stream.py` covers both stream routes.
- `tests/test_gate.py` covers decisions and reads at each scope.
- `tests/test_trace_withholding.py` covers the snippet route, the decisions route and the investor
  stream.
- `tests/test_audit.py` covers audit and export.
- `tests/test_synthesis.py` covers the grade decision route.
- `tests/test_cutover.py` covers `/`.

- `tests/test_auth_matrix.py` covers who may call every route, the token rules, the secret,
  `DELETE /api/runs`, what a token-less read carries, and the routes the ledger had listed as
  untested (`/api/chat`, `/api/config`, replay, contradictions, drift, `/api/self-report`,
  `/api/directive`). It walks `app.routes`, so a route without a row fails.
- `tests/test_fa_agent.py` covers the analysis routes.

### Limits and open issues

- [consistency-probe-shown-as-retry](ledger.html#consistency-probe-shown-as-retry) (medium, open).
  The chat route passes the same wrapped adapter to `run_consistency_probe`, so the probe's call is
  recorded as "LLM attempt 2", and the live view says "Retrying (attempt 2)". Found 2026-09-27 on
  chat run `3f1b0f89`, whose reasoning objects hold a single successful attempt. The trace and the
  record disagree (P6). Not fixed.
- [session-scope-leak](ledger.html#session-scope-leak) (high, open). Storage holds the full nested
  session, and reads redact it. The live responses of `/api/chat` and `/api/compare` to an
  investor went out with the nested session at full detail unless the run was awaiting a
  decision; found and fixed 2026-10-26 (both now go through `redact_for_scope`), with a test per
  route. Still open because storage is unchanged.
- [audit-criticals](ledger.html#audit-criticals) (critical, open). Addressed in code on
  2026-10-26 (see [[web/auth.py]]); storage-time redaction remains.
- `/api/chat/stream` and the chat payload's `steps` aren't filtered by scope. A step's `error` for
  a parse failure can quote model output; [[validation/gate.py]] removes that text from compare
  traces for investors ("parse-failure messages can quote model output", 2026-09-26 correction
  entry). Whether an investor chat response exposes internal-tier text this way hasn't been
  tested.
- **Replay has four problems.**
  - It runs on the subject alone, with an empty context, so a run that was given context is
    replayed on different input.
  - The expression that picks `directive_version` is `(A or B) if isinstance(session, dict) else
    None`, by Python's precedence rules. `A` is `config_snapshot["directive_version"]`, which
    `_config` never contains, so in practice it reads the session's version.
  - Compare runs store no `config_snapshot`, so replaying one rebuilds the adapter from an empty
    config.
  - [ollama-determinism](ledger.html#ollama-determinism) (open) says byte-identical replay
    shouldn't be assumed for a local model.
- `/app` is mounted only if `web/frontend/dist/` exists when the module is imported, but `/`
  checks for the folder on every request. If the UI is built after the server starts, `/`
  redirects to an `/app/` that returns 404 until a restart.
- [cached-classic-ui](ledger.html#cached-classic-ui) (low, open). A browser that cached the old
  `/` can keep showing the archived UI until it revalidates. `/` now sends `no-store`, so the
  problem can't recur for a later cutover.
- [ollama-hangs-under-compare](ledger.html#ollama-hangs-under-compare) (high, open). Four
  observations of the local model server hanging during compare runs. The model-call timeout
  (`MODEL_TIMEOUT_S`, in [[adapters/langchain_adapter.py]]) ends a hung call as a
  `LangchainConnectionError`, which the handlers above record as halted. The hang's cause is
  unconfirmed, and `CROSS_AGENT_MAX_CONCURRENCY=1` hasn't been tried.
- **Stale descriptions elsewhere.**
  - The module docstring says to run the server from "the accountability_layer/ directory"; the
    folder was renamed to `verification-layer/` (RUN_LOG 2026-08-21, "Rename accountability-layer/
    to verification-layer/").
  - `docs/SYSTEM_DESIGN.md` §3.6 says the two agents run "sequentially, not concurrently", and
    shows `agent_a_provider` and `agent_b_provider` request fields. The agents run at the same time
    by default (since the 2026-09-24 BL entry), and `CompareRequest` has no provider fields.
  - `docs/SYSTEM_DESIGN.md` §2.1 shows the chat request as `{ message, scope, provider?, model? }`.
    The body is `{message, context}`, and the scope comes from the token.
- `_config` is process-global. Two callers changing it race with each other, and with any run
  that is in progress and reads it.
- The startup handler hides any failure of the legacy JSON migration.
- FastAPI's `@app.on_event("startup")` is deprecated in favour of lifespan handlers. It still
  works; nothing here depends on the difference.

Related: [[web/auth.py]], [[web/db.py]], [[web/step_trace.py]], [[web/self_report.py]],
[[validation/gate.py]], [[validation/cross_validation.py]], [[validation/audit.py]],
[[adapters/registry.py]], [[adapters/langchain_adapter.py]], [[pipeline/middleware.py]]

## `web/step_trace.py`

**Role:** the ordered, timed record of what happened during one run: every fetch, LLM attempt,
tool call, extraction call and comparison, tagged with the phase it belonged to. It also provides
the two wrappers that turn adapter and model calls into steps.

### Why it exists

The module docstring explains it. A comparison result says what the comparator concluded, not
how either agent got there. The UI needs to show which calls were made, in what order, and which
were shared between the two agents and which belonged to one. That last distinction matters
because `/api/compare` makes exactly one SEC EDGAR fetch and derives both contexts from it. A UI
that drew a fetch under each agent would invent a second fetch. Phases exist so shared work is
recorded once, as `shared`, and can be drawn once.

### Phases and kinds

| Phase constant | Value | Used for |
|---|---|---|
| `PHASE_SHARED` | `shared` | work done once for both agents: CIK lookup, companyfacts fetch, per-lens summaries, the generic "shared context" note |
| `PHASE_AGENT_A` | `agent_a` | agent A's LLM attempts, tool calls, assessment extraction |
| `PHASE_AGENT_B` | `agent_b` | the same for agent B |
| `PHASE_COMPARE` | `compare` | the comparison note |
| `PHASE_CHAT` | `chat` | `/api/chat`'s single agent |

The step's `kind` is set by callers through `extra`, so the UI never parses a label:

| Kind | Set by |
|---|---|
| `fetch` | the server's shared EDGAR steps |
| `llm` | `wrap_adapter` |
| `extract` | `wrap_model_call` |
| `tool` | the server's tool-event recorder |
| `compare` | the server's comparison note |

The summarise steps and the generic "shared context" note carry no `kind`.

### A step

Every step is a dict with these fields:

| Field | Meaning |
|---|---|
| `seq` | the order steps *started* in, from 1, assigned under the lock |
| `phase` | the phase |
| `label` | human-readable name, e.g. `LLM attempt 1` |
| `started_at` | UTC wall-clock time, ISO-8601 to the millisecond |
| `detail` | optional text (a resolved CIK, a model and directive, a search query and result preview) |
| `url` | optional URL |
| `status` | `ok`, `error` or `parse_failure` (tool steps pass the adapter's own status) |
| `error` | `"Type: message"` on failure |
| `duration_ms` | wall-clock duration from `time.monotonic()`, to 0.1 ms |

Any `extra` fields follow. `extra` is merged over the fields above, so a key in `extra` with the
same name replaces the default.

The docstring is explicit about what the duration measures. It is enough to see that an LLM call
took seconds and a summary took microseconds. It is not a profiler, and it isn't comparable
across machines.

### Key functions

- `StepTrace.record(phase, label, *, detail, url, extra)`: a context manager that times a step.
  The step is created and `step_started` is published. The caller can change the yielded dict
  (for example, to set the resolved CIK as `detail`). On exit `duration_ms` is set and
  `step_finished` is published. If the body raises, the step is marked `error` with the exception
  text, unless the caller already set a more specific status such as `parse_failure`, and the
  exception is re-raised. The docstring gives the reason: a step that blew up is more interesting
  than one that never appears.
- `StepTrace.note(phase, label, *, detail, url, status, duration_ms, extra)`: records an instant
  fact, publishing only `step_finished`. `duration_ms` is for a caller that timed the work itself:
  tool calls are timed inside [[adapters/langchain_adapter.py]]'s async tool loop, which this class
  can't wrap.
- `StepTrace.subscribe(listener)`: registers `listener(event, step)`, called with
  `step_started` or `step_finished` and a *copy* of the step, so an observer can't change the
  record it is watching. A listener that raises is ignored. The code comment gives the reason: a
  broken observer, such as a browser that disconnected mid-stream, must never break or alter the
  run. The `/stream` routes in [[web/server.py]] subscribe their `emit` here.
- `StepTrace.to_list()`: a new list of the steps, taken under the lock. The step dicts in it are
  the live ones, not copies.
- `wrap_adapter(trace, phase, adapter, *, model_label)`: wraps an agent adapter so each call
  becomes a timed `LLM attempt n` step. The detail is `model=... · directive=<version>`, and the
  extras are `kind: "llm"`, `attempt` and `model`.
  - Its docstring explains the design. `run_validation_loop` calls the adapter once per attempt
    and passes the directive for that attempt, so the ADR-07 corrective retry shows up as attempt 2
    with directive `corrective`, without [[pipeline/middleware.py]] knowing about the trace.
  - After a successful call it runs `reject_unusable_conclusion` from [[core/parsing.py]], the
    same check the middleware makes. The step then reads `parse_failure` when the conclusion
    repeats the directive, instead of `ok`.
  - A `StructuralParseError` marks the step `parse_failure`; any other exception marks it `error`.
    Either way the exception is re-raised unchanged, so the retry and halt logic behaves as if the
    wrapper weren't there. The error type is matched by class name.
  - The attempt counter belongs to the wrapper, not to a run of the loop. Every call through the
    same wrapped adapter counts up.
- `wrap_model_call(trace, phase, call, *, model_label, prompt_version)`: wraps the
  assessment-extraction call as its own step. The label is `Assessment extraction`, the detail is
  `model=... · prompt=<version>`, and the extras are `kind: "extract"` and `model`. The docstring
  gives the reason: the trace shows that a second model call read the answer, and whether it
  failed, instead of the grade appearing from nowhere.

### Thread safety and ordering

The two compare agents run at the same time by default and append to one trace. A
`threading.Lock` guards only the `seq` counter and the list append. After creation, a step dict is
changed only by the thread that owns it (class docstring). `seq` plus `started_at` let the UI draw
two lanes running in parallel and still reconstruct what overlapped with what. With
`CROSS_AGENT_MAX_CONCURRENCY=1` the agents run A then B, and `seq` reads as a plain sequence.

Listeners are called after the lock is released. Two threads can therefore deliver events out of
`seq` order: a step with a higher `seq` can be published before one with a lower `seq`. A
consumer that needs start order should sort by `seq`, not by arrival.

`tests/test_concurrency_and_stream.py` tests this with fake steps:

- `seq` stays unique and gap-free across 8 threads;
- subscribers get copies;
- a broken listener doesn't break the run;
- the two agents really overlap (both must reach a `threading.Barrier`).

`tests/test_directive_echo.py` uses `wrap_adapter` to check that an echoed directive reads as
`parse_failure`.

### Scope of the instrumentation

The docstring says the instrumentation lives only in the route and in the adapter wrapper.
[[pipeline/middleware.py]] and [[core/schemas.py]] know nothing about it.
[[validation/cross_validation.py]]'s only concession is its optional `on_agent_finished` callback,
and it never imports this module. A search of the source outside `tests/` agrees: the only module
that imports `web.step_trace` is [[web/server.py]].

### Limits and open issues

- [consistency-probe-shown-as-retry](ledger.html#consistency-probe-shown-as-retry) (medium, open).
  Because the attempt counter belongs to the wrapper, the chat route's consistency probe, which
  reuses the wrapped adapter, is recorded as `LLM attempt 2` with the normal directive. It reads
  as a retry that never happened. Counting retries from reasoning objects instead of the trace is
  how [retry-halt-unproven](ledger.html#retry-halt-unproven) avoided this.
- Steps are delivered to listeners out of `seq` order under concurrency (above).
- `to_list()` returns the live step dicts. A caller that changes them changes the trace. The
  server only serialises them.
- `extra` can overwrite a step's core fields. The server avoids this for tool steps by naming the
  tool's phase `tool_phase`.
- `subscribe` isn't guarded by the lock. The server subscribes before any step is recorded, so
  this doesn't matter today.

Related: [[web/server.py]], [[pipeline/middleware.py]], [[core/parsing.py]],
[[validation/cross_validation.py]], [[adapters/langchain_adapter.py]]
