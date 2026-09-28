---
title: Root, configuration, docs and logs
slug: files-root
section: Files
order: 10
summary: The files at the top of verification-layer/, the design documents in docs/, and the run log.
---

This page covers everything in `verification-layer/` that is not code in a layer package: the
files at the root (instructions, dependencies, configuration templates, git settings, two
launch scripts, and a historical audit), the design documents in `docs/`, the archived video
scripts, this reference site, and `logs/RUN_LOG.md`, the subsystem's own run log.

None of these files is imported by the code. They matter because they are where the
subsystem's rules, configuration and history are written down, and several of them make
claims about the code that the code can be checked against. Where a claim here no longer
matches the code, the entry says so under "Limits and open issues".

How they fit together:

- `README.md` is the quick-reference layout and install guide. `docs/SYSTEM_DESIGN.md` is the
  long-form design reference. `CLAUDE.md` holds the working rules for anyone, human or agent,
  editing this folder.
- `requirements.txt` and `.env.example` are the install-time and run-time configuration.
  `.gitignore` and `.gitattributes` decide what git tracks and how it checks files out.
- `logs/RUN_LOG.md` is the append-only record every other document cites for dates and
  reasons.

Several files named in these documents live in `divij/`, the author's working folder. It is
listed in `.gitignore` (`/divij`), so those files are not distributed with the repository and
links to them from tracked files do not resolve on a fresh clone. The weekly walkthrough decks
in `docs/` (`docs/*.html` other than `docs/index.html`) are gitignored the same way.

## `__init__.py`

**Role:** an empty file that marks the subsystem root as a Python package.

The file has no content. The test command in `CLAUDE.md`
(`python -m unittest discover -s tests -t .`) sets the subsystem root as the top-level
directory, so modules are imported as `core.parsing`, `tests.test_x` and so on, with the root
itself on `sys.path`.

### Design notes
Why the root carries an `__init__.py` is not stated in the code or the log (judgment, not
recorded). The `logs/RUN_LOG.md` entry of 2026-08-14 ("Integrate accountability-layer into
Mycroft (self-contained)") notes that the folder's hyphenated name means no other project in
the repository can import it as a package, so the file does not make the subsystem importable
from outside.

## `README.md`

**Role:** the repository's storefront: what the system does, its architecture as Mermaid
diagrams, walkthroughs from recorded real runs, the quickstart, resilience and edge cases, the
layout, and a link to this site.

Rewritten on 2026-09-27 to showcase the project (`logs/RUN_LOG.md`, "GitHub Pages, CI, Docker and
a showcase README"). Sections, in order:

- **Title, badges, value proposition.** Badges for CI, the published docs, Python 3.10+ and the
  prototype status. An HTML comment marks where a demo GIF can go.
- **Callouts.** `[!IMPORTANT]` localhost only, linking `audit-criticals`. `[!NOTE]` what it doesn't
  claim.
- **Architecture.** A layer flowchart with the external services, a sequence diagram of a ticker
  compare, and state diagrams of the ADR-07 loop and the gate. They are drawn from
  [Layers and components](architecture.html) and [A run end to end](lifecycle.html).
- **Walkthroughs.** Each is taken from a fixture in `web/frontend/tests/fixtures/`, with its run id
  and date:
  - the gated Pokémon run `ec1a3b44`, including the investor view and a decision request with a
    real refusal message;
  - AAPL `20c538e4`, with its figure rows and accounting checks;
  - AAPL `8ecb0922`, with its grades and synthesis. It carries a warning that this run used
    extraction v1, which the record found unreliable;
  - the Markdown export.
- **Quickstart.** Three routes:
  - the sandbox: the offline suites, the docs build and the frontend verify, with no model or
    keys;
  - live, with Ollama;
  - Docker.

  It explains that there is no mock mode, per [D-17](decisions.html#d-17).
- **Resilience and edge cases.** A table of failure, mechanism and evidence. Each row says whether
  it is tested, observed (with the record's numbers), only in the code, or open.
- **Project layout, Documentation, Status.**

### Design notes
- The rewrite follows a showcase blueprint the human supplied (value proposition, architecture
  diagram, quickstart, resilience). Two of the blueprint's suggestions were declined, both by the
  human on 2026-09-27:
  - a mock mode, which conflicts with D-17;
  - a move into `src/`, which would break the layering test and the monorepo path.
- Numbers in the README come from the record: the retry and halt counts, the four Ollama hangs,
  and the stream overlaps. It gives no test count, because the CI badge and this site's
  generated counts stay current and a hand-written number wouldn't.

### Limits and open issues
- **The badges show no status** until the workflows first run on GitHub. The docs badge links to
  the Pages site, which exists only once Pages is enabled.
- **The illustrative decision** in walkthrough 1 is not a recorded decision. The run was still
  awaiting a real reviewer when the README was written.
- **Fixed by the rewrite** (the previous README's drift): it listed the archived adapters as live
  and the removed `failure_mode`, gave a stale "242 tests", named two contracts, omitted
  `filings.py` and the bull/bear lenses, and restated four of the audit's five critical findings.
  The previous README is in git history.

Related: [[CLAUDE.md]], [[.github/workflows/ci.yml]], [[.github/workflows/pages.yml]]

## `CLAUDE.md`

**Role:** the working rules for anyone, human or AI agent, changing files in this folder.

It adds subsystem rules on top of the repository-root `SNICKERDOODLE.md`, `CLAUDE.md` and
`AGENTS.md`, and says it does not override them. The rules:

- **Two repositories, one folder.** The subsystem lives at `verification-layer/` on
  `nikbearbrown/mycroft`'s `feature/accountability-layer` branch, and at the root of
  `divij-pawar/mycroft-accountability`'s `main`, kept in sync with `git subtree`. Syncs run from
  a monorepo clone (`subtree pull` to bring standalone changes in, `subtree push` to send
  monorepo changes out). Never push to the monorepo's `main`; never `--squash`; no
  force-pushes; an agent pushes only when asked for that push. Gitignored data doesn't sync. A
  fuller guide may sit in `.claude/git-workflow.md`, which is local and gitignored. Set up on
  2026-09-27 (`logs/RUN_LOG.md`).
- **Log every meaningful change** in `logs/RUN_LOG.md`, in the format Recipe, Inputs,
  Commands, Outputs, Result, Open issues. If a Claude agent made the change, also add an entry
  to `divij/work.md`, claiming AI authorship only with evidence (the session itself, or a
  `Co-Authored-By:` trailer). Never backdate, edit or delete an earlier log entry; correct it
  with a new one.
- **Stay self-contained.** Modify no file outside this folder. Add a non-stdlib dependency only
  as a documented "deliberate exception" in the importing file's docstring. Never commit
  `env/` or `.env`, and never quote `.env`.
- **Test before claiming done.** Run `python -m unittest discover -s tests -t .` (network-free
  and model-free by design) and `node scripts/conformance.mjs` from the repository root, scoped
  to the changed files.
- **Writing a video script.** Follow `divij/video-script-writing-guide.md`.
- **Don't overclaim.** State whether a capability was built or observed. Check claims against
  `divij/cross-agent-validation-proposal.md` §9 and `divij/sdd.md` §14.

### Design notes
The log entry of 2026-08-28 ("Add subsystem-scoped AGENTS.md / CLAUDE.md") records that an
`AGENTS.md` plus a one-line `CLAUDE.md` was written first, then, the same session, merged into
`CLAUDE.md` alone at the user's request. There is no `AGENTS.md` in this folder.

### Limits and open issues
- Every `divij/` file it points to (`work.md`, the video guide, the proposal, the SDD) is
  gitignored, so the links do not resolve outside the author's machine.
- "Network-free" in the test rule: `tests/test_model_timeout.py` (untracked at the time of
  writing) runs a stub server on 127.0.0.1, per the 2026-09-27 timeout entry. That is loopback,
  not an external call, but it is a local socket.

## `requirements.txt`

**Role:** the pip dependencies for the web app and the live model path.

The header says the core engine (parser, middleware, schemas, directive) is stdlib-only and
needs none of it, so the tests run on a bare interpreter; it asks for Python 3.10 or later and
says it was tested on 3.12.6. Groups:

| Group | Packages | Why, per the file's comments |
|---|---|---|
| Web app | `fastapi`, `uvicorn[standard]`, `pydantic`, `PyJWT`, `python-dotenv` | `web/server.py` and `web/auth.py`; `pydantic` is imported directly, not only via FastAPI; `PyJWT` for scope tokens; `python-dotenv` for `.env` loading. |
| LangChain | `langchain-core`, `langchain-ollama`, `langchain-google-genai` | The one agent framework. A model name containing "gemini" selects the Google package, anything else Ollama; both are imported lazily, so only the family in use must work. |
| Agent tool | `langchain-tavily` | Tavily search, the agent's tool. Needs `TAVILY_API_KEY`. The comment says a search call fails loudly without it; the code now skips tools instead (see [[.env.example]]). Replaced an earlier MCP fetch tool. |
| Observability | `langfuse>=3.0.0` | Self-hosted tracing; v3 API only; a no-op without credentials. |

All versions are lower bounds (`>=`); nothing is pinned.

### Limits and open issues
- Line references in the comments are stale: `middleware.py:57` (cited for a PEP 604 union)
  is now the start of `_build_reasoning_object` in `pipeline/middleware.py`, and
  `web/server.py:34` (cited for the `pydantic` import) is now line 36.
- The observability comment points to `financial_grader.py`, renamed in the 2026-09-04
  restructure; the producer is now `producers/financial.py`.
- `google-genai`, needed by the archived Gemini adapter, is not listed. That is consistent
  with the archive (nothing live imports it), but restoring that adapter would need it.

## `.env.example`

**Role:** the template for `.env`, listing every environment variable a user is expected to
set.

Copy it to `.env` and fill it in. `.env` itself is gitignored here and at the repository
root. The variables, in file order:

| Variable | Default in the template | Read by | Meaning |
|---|---|---|---|
| `LANGCHAIN_MODEL` | `llama3.2` | `web/server.py` (default config) | The model LangChain drives. A name containing "gemini" selects the Gemini family; anything else selects Ollama. |
| `MODEL_TIMEOUT_S` | `120` | `adapters/langchain_adapter.py` | Seconds a model call may receive nothing before it is abandoned and the run recorded as halted. For Ollama, which streams, a stall limit rather than a cap on call length. |
| `GEMINI_API_KEY` | empty | `adapters/langchain_adapter.py` | Google AI Studio key; needed only for a Gemini-family model. |
| `TAVILY_API_KEY` | empty | `adapters/langchain_adapter.py`, `adapters/registry.py` | Key for the agent's search tool. If empty, the adapter runs that call without tools and warns. |
| `SEC_USER_AGENT` | empty | `datasources/edgar.py`, `datasources/filings.py` | The contact string SEC's fair-access policy asks for. If unset, a placeholder is used, which SEC may throttle. |
| `LANGFUSE_HOST` | `http://localhost:3000` | LangFuse SDK, `scripts/smoke_langfuse_trace.py` | Self-hosted LangFuse URL. |
| `LANGFUSE_PUBLIC_KEY` | empty | LangFuse SDK, `scripts/smoke_langfuse_trace.py` | LangFuse project public key. |
| `LANGFUSE_SECRET_KEY` | empty | LangFuse SDK, `scripts/smoke_langfuse_trace.py` | LangFuse project secret key. With either key unset, tracing is disabled without errors. |

The `MODEL_TIMEOUT_S` block is an uncommitted working-copy change at the time of writing. The
reason for 120 s, in the template and in the 2026-09-27 timeout entry of `logs/RUN_LOG.md`:
it is above the slowest successful attempt stored in the run database, 85.3 s.

### Limits and open issues
Three variables the code reads are not in the template (found by searching the code for
`os.environ`):

- `OLLAMA_HOST` (`adapters/langchain_adapter.py`, default `http://localhost:11434`).
- `CROSS_AGENT_MAX_CONCURRENCY` (`validation/cross_validation.py`, default 2).
- `ACCOUNTABILITY_SECRET` (`web/auth.py`), the JWT signing secret, which falls back to a
  built-in development value. The audit's §7 item 2 asked for it to be added here; it has
  not been.

The `TAVILY_API_KEY` comment says that without a key "the tool still binds, but a search call
will fail". The code no longer does that: `adapters/langchain_adapter.py` checks for the key
before each call and, if it is missing, runs the call without tools, because Tavily's
constructor raises at once without a key (the code comment, and `logs/RUN_LOG.md` 2026-09-23,
"Make LangChain the sole agent framework..."). `requirements.txt` repeats the stale wording.

The LangFuse setup comment says `cd ../.langfuse`; `scripts/smoke_langfuse_trace.py` prints
`cd .langfuse`. Neither folder is inside this subsystem.

## `.gitignore`

**Role:** this subsystem's own ignore rules, kept here so nothing has to be added to the
repository-root `.gitignore`.

What it keeps out of git:

- **Python and editor artefacts:** `__pycache__/`, `*.pyc`, `.env`, `/.claude`.
- **The run store:** `web/data/*.db`, its SQLite WAL sidecars (`*.db-shm`, `*.db-wal`),
  `web/data/*.json` and `*.json.migrated`. The comment: "runs are reproduced by replay, not by
  commit".
- **Filing cache:** `web/data/filing_cache/`, filings fetched from SEC on demand by
  `datasources/filings.py`, which can be fetched again.
- **Local-only folders:** `/env` (the virtualenv), `/openclaw`, `/youtube`, `/context`,
  `/divij`.
- **Walkthrough decks:** `/docs/*.html`, with `!/docs/index.html` re-including the index.
  Because the pattern is anchored to `docs/`, the built reference site under
  `docs/reference/` is not ignored (checked with `git check-ignore`).
- **Stray pip artefacts:** `=*` (for example a file literally named `=2.8.0`).
- **React build output:** `web/frontend/node_modules/`, `web/frontend/dist/`,
  `web/frontend/*.tsbuildinfo`.
- **Packaging output:** `*.egg-info/` and `/build/`, added on 2026-09-27 with
  [[pyproject.toml]], which makes `pip install .` possible.

### Design notes
The `/docs/*.html` rule replaced an earlier rule that ignored all of `/docs`, which would have
silently dropped design documents moved there (`logs/RUN_LOG.md`, 2026-09-04, "SOLID
restructure...", under "Also fixed").

### Limits and open issues
The WAL-sidecar comment contains a replacement character (U+FFFD) where a dash was intended.

## `.gitattributes`

**Role:** makes git check test fixtures out byte-exact.

Two rules: `web/frontend/tests/fixtures/** -text` and `tests/fixtures/** -text`. With `-text`,
git does no line-ending conversion on these files.

### Design notes
Added 2026-09-27 ("Committing the work since c53746a, and a checkout-only test failure"). On a
clean checkout with `core.autocrlf=true`, the SSE fixture
`web/frontend/tests/fixtures/stream_compare_aapl_2026-09-24.txt` came out with CRLF endings,
and one frontend test failed because the stream parser splits events on `\n\n` only. The same
entry records the fix re-checked: a fresh checkout is LF and `npm run verify` passes.

### Limits and open issues
The same entry notes that the parser in `web/frontend/src/api/stream.ts` still does not
accept CRLF or CR line endings, which the SSE specification allows. The server sends LF, so
this is not hit today.

## `start-web-server.sh`

**Role:** a three-line launcher that starts the API server under WSL.

It changes to the hard-coded path `/mnt/d/Code/mycroft/verification-layer` and runs
`python3 -m uvicorn web.server:app --reload --port 8000`. It does not activate a virtualenv
and does not build the React app.

### Limits and open issues
- It only works on a machine where the repository is at that exact WSL path.
- It duplicates [[scripts/start-server.sh]], which resolves its own location, activates `env/`
  if present, builds the React app if it is missing, and honours `PORT`.
- No entry in `README.md`, `CLAUDE.md` or `logs/RUN_LOG.md` mentions it. Git history shows it
  first in commit `db54d62` (2026-09-26, "Restructure verification-layer into layered
  packages"). Why it was kept alongside `scripts/start-server.sh` is not recorded (judgment,
  not recorded).

## `.github/workflows/ci.yml`

**Role:** continuous integration on every push and pull request. It runs the Python suite, the
docs coverage check, and the review app's type-check, tests and build.

- **`python` job,** on Python 3.10 and 3.12 (the supported floor and the version the suites are
  usually run on):
  - `pip install -r requirements.txt`;
  - `python -m unittest discover -s tests -t .`;
  - `python scripts/build_docs.py --check`.
- **`frontend` job,** on Node 20: `npm ci`, then `npm run verify` in `web/frontend`.
- Neither job needs a model, the network beyond package installs, or a secret. The suites are
  network-free and model-free by design.
- The README's CI badge reads this workflow.

### Limits and open issues
- It hadn't run on GitHub when it was written (2026-09-27): it was checked by parsing it and by
  running the same commands locally.
- In the `nikbearbrown/mycroft` monorepo, this file sits at `verification-layer/.github/` and is
  inert, because GitHub reads workflows only from the repository root. The monorepo's own CI is
  its root `verify.yml`.

Related: [[.github/workflows/pages.yml]], [[tests/test_docs_coverage.py]]

## `.github/workflows/pages.yml`

**Role:** publishes this reference site to GitHub Pages, at
`https://divij-pawar.github.io/mycroft-accountability/`.

- Triggers on a push to `main`, or by hand (`workflow_dispatch`).
- The build job runs `python scripts/build_docs.py`, which regenerates the code-derived parts
  and exits non-zero on any problem, so a broken site never deploys. It then uploads
  `docs/reference/` as the Pages artifact.
- The deploy job publishes it with `actions/deploy-pages`. Permissions are `pages: write` and
  `id-token: write`; concurrency group `pages`.
- **One-time setup, by hand:** Settings → Pages → Build and deployment → Source: "GitHub
  Actions".

### Limits and open issues
- **Local-only links 404.** The published site has no week 1–3 walkthroughs, because they're
  gitignored, so those History links 404 there.
- **Everything is public.** The site includes the Honest Ledger's open security findings.
  Publishing them was the human's decision on 2026-09-27; the repository is public.
- It hadn't run on GitHub when it was written.
- Inert in the monorepo, like [[.github/workflows/ci.yml]].

Related: [[scripts/build_docs.py]], [[docs/reference/**]]

## `Dockerfile`

**Role:** one image with the API server and the built review app. The model runs outside it.

- **Stage 1** (`node:20-slim`): `npm ci` and `npm run build` in `web/frontend`.
- **Stage 2** (`python:3.12-slim`): installs `requirements.txt`, copies the source and the built
  `dist/`, exposes 8000, and runs `uvicorn web.server:app --host 0.0.0.0`.
- `OLLAMA_HOST` defaults to `http://host.docker.internal:11434`, so agents reach Ollama on the
  host. `.env` is passed with `--env-file` at run time and never copied in (see
  [[.dockerignore]]).
- The documented run command publishes the port on `127.0.0.1` only, because the routes are
  partly unauthenticated ([audit-criticals](ledger.html#audit-criticals)).

### Limits and open issues
- **Not yet built.** The Docker engine wasn't running on the machine where it was written
  (2026-09-27), so the image hasn't been built or run.
- `host.docker.internal` resolves on Docker Desktop. On Linux, `docker run` needs
  `--add-host=host.docker.internal:host-gateway`.
- The run store is written inside the container (`web/data/`) and is lost with it, unless a
  volume is mounted there.

Related: [[.dockerignore]], [[requirements.txt]], [[web/server.py]]

## `.dockerignore`

**Role:** keeps secrets, local data and rebuildable output out of the Docker build context.

It excludes `.env`, `.git`, `.github`, `.claude`, `env/`, `__pycache__`, `web/data/`, the
frontend's `node_modules/` and `dist/` (both rebuilt inside the image), the local-only folders
`divij/`, `context/`, `openclaw/` and `youtube/`, and the gitignored `docs/*.html` walkthroughs.

Related: [[Dockerfile]], [[.gitignore]]

## `pyproject.toml`

**Role:** project metadata in the standard format: name, description, `requires-python >= 3.10`,
and links to the repository and the docs.

- Dependencies are `dynamic`, read from [[requirements.txt]], so that file stays the single
  source.
- Packages are listed explicitly: `core`, `adapters`, `pipeline`, `datasources`, `producers`,
  `validation`, `web`.
- It isn't published anywhere. The documented install remains `pip install -r
  requirements.txt`.

Related: [[requirements.txt]], [[README.md]]

## `docs/DATA_CONTRACT.md`

**Role:** the data contract for the run store, scoped to this subsystem.

The repository-root `DATA_CONTRACT.md` governs Mycroft's `data/raw/` to `data/verified/`
layers. This file covers only the run store (the SQLite database of agent attempts), which is
machine-local and enters neither layer. It has one table row and a list of rules:

- **Dataset:** `runs`, `sessions`, `flags`, from live agent attempts through
  `run_validation_loop`, one `ReasoningObject` per attempt, stored in `web/data/*.db`
  (gitignored). Nothing has been promoted to `data/verified/`.
- **Gates:** a machine structural-parse gate (two failed parses halt the run, recorded as
  `HALT`) and a human attestation gate, "not yet defined": no run has been attested. Owner:
  unassigned.
- **Rules:** append-only by SQLite triggers, but not tamper-evident, because the
  unauthenticated `DELETE /api/runs` still drops the tables; the database may hold full raw
  model output and is sensitive; reproduction is by replay, not by committing the database;
  no invented counts; no secrets in tracked files.

It moved from the subsystem root into `docs/` in commit `99aec80` (2026-09-26).

### Limits and open issues
- It names old module paths: `middleware.run_validation_loop` (now
  `pipeline/middleware.py`) and `parser._parse_response` (now `core/parsing.py`).
- The dataset list predates the `gate_decisions` table in `web/db.py` (the human decisions,
  also append-only by trigger), and the reviewer-flag table is `reviewer_flags`, not `flags`.
- It cites `accountability-layer-audit.md`, removed from the repository on 2026-09-27 (see
  [the prototype audit](history.html#the-prototype-audit-accountability-layer-audit-md)).

## `docs/SYSTEM_DESIGN.md`

**Role:** the long-form design reference: system workflow, data model and design rationale for
the accountability layer and Cross-Agent Validation.

It dates itself "As of: 2026-09-14" and was written that day (`logs/RUN_LOG.md`, "Write
docs/SYSTEM_DESIGN.md: full system workflow and design-decision reference"). Its status rule:
every claim is either tested, tied to a cited live run, or marked not done. Sections:

1. **What these two layers are, and how they relate.** Cross-Agent Validation is one function
   that calls the accountability layer's `run_validation_loop` twice and compares the
   conclusions.
2. **Accountability layer workflow:** the `/api/chat` flow, the structural contract, ADR-07's
   retry-then-halt loop, directive versioning, confidence scoring, the partial mitigations for
   ADR-06 (claims, verification, consistency), persistence, and scope redaction with its
   known gap.
3. **Cross-Agent Validation workflow:** why it exists, the two producers, the comparison flow,
   the two contradiction rules, the shared number regex, and `POST /api/compare`.
4. **Design decisions and rationale:** a table (stdlib-only core, frozen dataclasses,
   `Protocol` contracts, hardcoded directives, trigger-enforced append-only, and others).
5. **Data model reference.**
6. **Test and verification discipline.**
7. **Honest status:** a snapshot of the Honest Ledger with severities and statuses.
8. **File layout reference.**
9. **Sources this document synthesizes.**

Other reference pages cite it by section number for design reasons.

### Limits and open issues
The document was edited again at the 2026-09-27 cutover (per the B6 + U9 entry) but its
snapshot parts were not refreshed:

- "242 automated tests" in the header and §6, and §8's "242 stdlib unittest tests"; the log
  now records 459.
- "Nothing in this subsystem is committed to git past commit `c53746a`". Commits `db54d62`
  to `c1dc294` (2026-09-26) now exist.
- §7's ledger snapshot predates later changes; for example the 2026-09-27 timeout entry moved
  `no-route-tests` and `retry-halt-unproven` to RESOLVED.
- §8 lists `adapters/` as "gemini, ollama, mock, fixture + registry.py"; three of those are
  archived. It lists `datasources/` as `edgar.py` only (there is also `filings.py`) and
  `validation/` without the files added from 2026-09-24 on.
- It cites `divij/` documents, which are gitignored.

For current status, the live ledger (`GET /api/self-report`, [[web/self_report.py]]) is the
source the document itself names.

## `docs/index.html`

**Role:** a standalone page that lists the weekly walkthrough decks in `docs/`.

It fetches `https://api.github.com/repos/nikbearbrown/mycroft/contents/verification-layer/docs`
in the browser, keeps the `.html` names, and draws one card per file, titled from the file
name. If that request fails, it falls back to a hard-coded list:
`accountability_layer_week1.html`, `inversion_audit_engine_week3.html`,
`validation_loop_walkthrough_week2.html`. The page has its own inline styles (a purple
gradient and system fonts).

It moved from the subsystem root into `docs/` in commit `99aec80` (2026-09-26).

### Limits and open issues
- The decks are gitignored, so they exist only on the author's machine. On GitHub, a
  successful API call can list only tracked `.html` files directly in `docs/`, which today is
  this index itself (judgment from the `.gitignore` rule; not tested against GitHub). The
  fallback list is used only when the request fails.
- The page makes a network request to GitHub when opened.
- Its colours are hard-coded rather than taken from `brutalist/DESIGN.md`. Whether DESIGN.md
  applies to this page is not recorded; the 2026-09-24 decision entry ("Human decisions:
  audit-layer roadmap approved...") excludes only the web app from DESIGN.md.

## `docs/video/archive/*`

**Role:** two archived narration scripts for a two-part explainer video series, "Building an
audit layer for AI agents".

Both scripts follow one structure: a production brief (audience, goal, glossary, scene map,
assets to capture, accuracy guardrails, tone, pronunciation, open decisions), then timed
chapters pairing narration with visuals, then a fact-check sheet mapping every claim to a
`logs/RUN_LOG.md` entry. The brand graphics follow `brutalist/DESIGN.md`; app screen captures
keep the app's own palette (a human decision dated 2026-09-24 in the scripts).

| File | Origin | Date | Covers | What uses it |
|---|---|---|---|---|
| `docs/video/archive/part-1-two-ais-agreed.md` | Part 1, "Two AIs agreed. That's the problem." | Sources: RUN_LOG 2026-09-24 to 2026-09-25 | Why two agents agreeing is not evidence: period- and unit-aware facts (B0), figure-by-figure comparison (B1 + U2), concurrent runs (BL), and the first same-figure match (B2 + B3). | Nothing in the code. Reference for video production. |
| `docs/video/archive/part-2-who-gets-the-last-word.md` | Part 2, "Who gets the last word." | Sources: RUN_LOG BG + U3 and B2 + B3 (2026-09-25), plus roadmap items not yet logged | The human decision gate, accounting checks, sources located in filings, and assessments and grades. | Nothing in the code. Reference for video production. |

Both were added in commit `99aec80` (2026-09-26), whose message calls them "earlier video
scripts ... archived". No log entry records why they went straight to an archive folder
(judgment, not recorded).

### Limits and open issues
- Part 2's fact-check sheet leaves several claims marked `**[verify]**` (BP, U4, B4 to B6,
  U5 to U9), because they were not yet in the run log when it was written. One figure
  ("Bull BBB / Bear BB") is marked illustrative. The 2026-09-26 entry "B4 + U7: structured
  assessments and bull/bear, built and reverted as the default" means the bull/bear material
  should be checked against that entry before any recording.
- `CLAUDE.md` says scripts follow `divij/video-script-writing-guide.md`, which is gitignored.

## `docs/reference/**`

**Role:** this reference site: its Markdown sources, its stylesheet and scripts, and its built
HTML.

| Path | What |
|---|---|
| `docs/reference/src/AUTHORING.md` | the format and accuracy rules for writing entries |
| `docs/reference/src/pages/*.md` | the narrative pages: overview, architecture, features, reference, history |
| `docs/reference/src/files/*.md` | one page per layer or folder, one entry per project file |
| `docs/reference/assets/site.css` | the styles: the six `brutalist/DESIGN.md` colour tokens, light by default, with its dark-mode values under `data-theme="dark"`; EB Garamond, Inter and JetBrains Mono; no sticky header |
| `docs/reference/assets/site.js` | the theme toggle (remembered in `localStorage` as `docs-theme`), the mobile menu, and search (press `/`; arrow keys and Enter to pick a result) |
| `docs/reference/assets/search-index.js` | generated: every page, section, file, function, class, export, route and ledger entry |
| `docs/reference/*.html` | generated: one page per source |

- **Built by** [[scripts/build_docs.py]]; **checked by** [[tests/test_docs_coverage.py]].
- **The built output is committed**, so the site opens straight from disk. The `/docs/*.html`
  rule in [[.gitignore]] matches only files directly in `docs/`, so it doesn't reach
  `docs/reference/`.
- **Fonts** load from Google Fonts. Offline, the pages fall back to the stacks DESIGN.md names.
- **Written with help.** On 2026-09-27 the per-file entries were drafted by AI subagents, each
  given one folder and AUTHORING.md, then checked against the code by the session that assembled
  them. Each entry names its sources; `logs/RUN_LOG.md` records the build.

## `logs/RUN_LOG.md`

**Role:** the subsystem's append-only run log: the dated record of every meaningful change,
run, finding, decision and correction, and the source every other document cites for "when"
and "why".

The header explains why it exists apart from the repository-root log: the subsystem is
self-contained, and keeping its history here avoids conflicts with the other authors who
append to the shared file.

### Entry format
Each entry is a level-2 heading, `## YYYY-MM-DD -- Title`, with `(continued)` after the date
for later entries the same day. The body is a bullet list with these bold labels, as
`CLAUDE.md` requires:

- **Recipe:** what was requested or run, and any decision taken with the user first.
- **Inputs:** the files and data read.
- **Commands:** what was run, including test and conformance commands and their counts.
- **Outputs:** files new, moved and changed.
- **Result:** what was observed.
- **Open issues:** what is still unknown, unverified or broken.

Entries from 2026-09-24 on often add labelled sub-sections (for example the roadmap phase,
"Live", "Cutover", "Corrections") between these. Corrections never edit an earlier entry; a
later entry states what was wrong (for example 2026-09-26, "Correction: two claims in earlier
entries were wrong...", and the 2026-09-27 timeout entry's "Corrections (earlier entries
unchanged)").

### Roadmap codes
From 2026-09-24 the log names work by the phases of an approved plan (recorded in "Human
decisions: audit-layer roadmap approved; web UI scope for brutalist/"). `B` codes are backend
phases (`BL` the concurrency and live-stream work, `B0` to `B6`, `BG` the decision gate, `BP`
filing location) and `U` codes are UI phases (`U1` to `U9`).

### Major phases
Entries are listed by date and title so they can be found by searching the heading.

**Integration and rename (2026-08-14 to 2026-08-21)**

- 2026-08-14: Integrate accountability-layer into Mycroft (self-contained).
- 2026-08-21: Rename accountability-layer/ to verification-layer/; resolve duplicate subsystem
  paths after an upstream merge; implement Cross-Agent Validation v1 (SDD v1).

**A real second producer and the first live runs (2026-08-28 to 2026-08-29)**

- 2026-08-28: Replace fixture Producer B with a real second grader; configure
  `GEMINI_API_KEY` and add queryable contradictions, model heterogeneity and an HTTP route.
- 2026-08-29: first live-run attempts (two bugs fixed, one external blocker); the first
  observed live Cross-Agent Validation run; five model tests against the live AAPL result and
  their write-up; widening the number regex; comparator semantics for information-asymmetric
  agents; larger-sample results; a synthesis of the component's state.

**Instructions and video work (2026-08-28 to 2026-08-29)**

- Subsystem `AGENTS.md` / `CLAUDE.md`; a weekly-update video script and a script-writing
  guide; deleting and recreating the video project folder (recorded as destructive and
  user-confirmed); moving the script to its own folder; fixing links after `work.md` moved
  into `divij/`.

**Compare UI and the layered restructure (2026-09-04 to 2026-09-07)**

- 2026-09-04: a frontend for Cross-Agent Validation with an honest-ledger surface; Compare UI
  v2; "SOLID restructure: layered packages, no loose root modules, three duplications
  removed".
- 2026-09-07: a real-run regression corpus and a diagnosis of the disjoint-concepts over-flag;
  a video script for the period.

**Comparator correctness (2026-09-11 to 2026-09-14)**

- 2026-09-11: the concept-linkage prototype and the first overlapping-concept live test; then
  wiring concept linkage, the regex fix and fabrication detection into production.
- 2026-09-14: writing `docs/SYSTEM_DESIGN.md`.

**LangChain, no mocks, directives and tools (2026-09-22 to 2026-09-23)**

- 2026-09-22: remove `mock_adapter.py` and add the LangChain adapter; remove all mock and
  scripted features from the running app; several `/api/chat` and UI fixes; directives
  v1.2.0 and v1.3.0 (grounding, citation verification); an MCP fetch tool and logging of its
  calls.
- 2026-09-23: generalise beyond finance (superseded mid-implementation, kept); make LangChain
  the sole agent framework and replace the MCP tool with Tavily search; configure
  `TAVILY_API_KEY`; make the agent search first.

**The audit-layer roadmap begins (2026-09-24)**

- Role pills and visible citation status; stopping directive echo; same-source tests; the
  roadmap decisions; BL (concurrency and streaming); B0 (period- and unit-aware facts);
  directive v1.5.1 with a machine check; U1 (the React foundation); B1 + U2 (figure-by-figure
  comparison and the matrix UI).

**Gate, checks and sources (2026-09-25)**

- BG + U3 (the human decision gate); B2 + B3 (overlapping metrics and accounting checks); BP +
  U4 (figures located in the filing, live compare runs in `/app`).

**Review, corrections and grades (2026-09-26)**

- U5 + U6 (review summary, sources in place); a correction entry and its verification on a
  restarted server; B4 + U7 (assessments and bull/bear, built and reverted as the default);
  option 1 + B5 + U8 (grades, why they differ, who sets them).

**Cutover, commits and hardening (2026-09-27)**

- B6 + U9: the audit export, the remaining pages, the cutover of `/` to the React app and the
  archive of the classic UI.
- Committing the work since `c53746a`, and the checkout-only fixture failure that led to
  `.gitattributes`.
- A model-call timeout and a ledger refresh that found two wrong claims. This last entry is
  an uncommitted working-copy addition at the time of writing.

### Limits and open issues
- One entry is out of date order: "2026-08-28 -- Move the week-update script to its own video
  project folder" sits after several 2026-08-29 entries. The log is append-only, so it was
  left in place.
- Entries before the 2026-09-04 restructure name old flat module paths (`parser.py`,
  `financial_grader.py`); the restructure entry says the log was deliberately not rewritten.
- Several entries refer to plans and documents outside the repository or in `divij/`.

Related: [[CLAUDE.md]], [[web/self_report.py]]
