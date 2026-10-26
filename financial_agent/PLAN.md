# Financial agent: build plan

Status: **phase 1 built (2026-10-06); phases 2–7 not started.** Written 2026-10-06 from a
design review in a Claude Code session (the critique, the human's answers and the agreed
pushbacks are summarized in "Decisions" below). Every phase has a "done when" line that
separates *built and tested* from *observed on a live run* (see `CLAUDE.md`, "Don't overclaim").
Where building phase 1 changed the plan, section 13 says what and why; the sections above it
are kept as first written, so the record of what was planned survives.

## 1. Why this exists

The verification layer has only been exercised against weak agents and recorded or planted
data (`logs/planted-errors-audit.md`: 60 planted errors, 50 stopped, all on five recorded runs).
It can't be improved further without a **real financial agent on real data** to verify. This
component is that agent, and it is also user-facing: a user asks for a stock signal in a chat
interface and gets a signal with its reasons.

Two components, one boundary:

- **Financial agent** (this folder): takes a request, gathers data through recorded tools,
  produces a signal, and writes an **analysis record**.
- **Verification layer** (the rest of this repo): reads analysis records, checks them, and
  writes **verdict records**. It never imports this folder, and this folder never imports it.
  They share only the record schema, which lives in `core/`.

The folder lives inside `verification-layer/` for now so it syncs with the subtree. It will be
moved out in a later update; the boundary above is what makes the move cheap.

## 2. Decisions (2026-10-06)

| # | Decision | By |
|---|---|---|
| D1 | The financial agent is a separate component from the verification layer, in its own folder (`financial_agent/`) inside this directory for now. | human |
| D2 | One user type. The agent's output goes to that user and affects only them. The existing scope rule P1 ("only a grade a human set is shown to investors") governs compare runs; analysis runs are a new, user-owned record type shown to their user. Record this as a design decision on the reference site, so P1 isn't read as silently broken. | human |
| D3 | The progress screen shows the thought log in a light, non-detailed way. The Verification tab shows it in full. The progress screen is driven by the **step trace** and shows real values (data source and as-of date, indicators computed, chunks read, figures checked), so any confidence it gives is earned rather than staged. | human + review |
| D4 | Models without native reasoning (llama3.2, qwen2.5) are labelled as such. "Thinking" is disabled for them; their requested thought log is shown as **"Working notes."** "Thinking" is reserved for models that produce it natively. Session agents (Claude Code, Codex) show their **tool calls**. | human + review |
| D5 | The result is a signal card plus a two-tier verification verdict (section 6), not red/green on grade equality. | review, agreed |
| D6 | Inputs are frozen. Every analysis stores a bundle of the raw data and every tool call. Re-verification replays from the bundle, never from fresh data. | review, agreed |
| D7 | Tier-2 verification defaults to "original agent = A, pick B", plus a "same model ×N" option to measure the noise floor. | review, agreed |
| D8 | The "testing suite" means the **Verification tab** in the web app, not the unittest suite. | human |
| D9 | No investment amount. The chat box is free text; ticker and horizon are extracted from it or asked for. | human |
| D10 | Uploaded documents are user-asserted, fenced as untrusted, and can never satisfy a source check. Session agents run with their built-in shell/write tools off. Poisoned documents become verification test cases. | review, agreed |
| D11 | Long context comes from session agents (Claude Code, Codex) over the existing `/mcp` server. For analysis runs they get data **only through the layer's MCP data tools**. Using their own web search is allowed but marks the run `inputs_captured: false`. | human + review |
| D12 | Users can run an analysis with **their own agent** under a published contract (section 5.3). | human |
| D13 | It's not just an audit tool anymore. The chat is free-form, but **each signal is one run** with one frozen bundle, because verification needs a unit. Follow-ups that produce a new signal are child runs (`parent_run_id`). | human + review |
| D14 | Signal vocabulary: a 5-point ordinal (`strong_sell`, `sell`, `hold`, `buy`, `strong_buy`) plus horizon and conviction, in a closed vocabulary that is never coerced (like `core/assessment.py`). AAA–CCC, a credit-rating scale, is not used here. | review, agreed |

## 3. Layout and layering

```text
financial_agent/
  PLAN.md            this file
  __init__.py
  prices.py          EOD price datasource (cached; see 4.1)
  indicators.py      deterministic indicators, stdlib only
  bundle.py          builds, hashes and freezes the input bundle
  tools.py           the data tools (one implementation, served in-process and over MCP)
  replay.py          answers tool calls from a frozen bundle
  agent.py           the analysis loop: request → tools → signal → record
  prompts.py         versioned analysis prompt(s)
  documents.py       upload parsing and chunking (phase 6)
core/
  analysis.py        AnalysisRecord / VerdictRecord schema + signal vocabulary (shared)
validation/
  analysis_checks.py Tier-1 grounding of an analysis record against its bundle
  analysis_verdict.py Tier-2 verdict
web/
  server.py          new routes (section 7)
  mcp_server.py      new analysis tools (section 5.2)
  db.py              new append-only tables
  frontend/src/views/AnalysisView.tsx, VerifyView.tsx
```

Layering (`tests/test_layering.py` gets a `financial_agent` entry in its first code phase):

- `financial_agent` may import `core`, `adapters` and `pipeline`. It may **not** import
  `validation` or `web`.
- `validation` may **not** import `financial_agent`. It reads records only.
- `web` may import both (it is the composition root, as today).

Price data comes in through `financial_agent/prices.py`, not `datasources/`. That keeps the
component movable, but it means `datasources/` is no longer the only data egress. The README
and the docs entry for `datasources/` must say so.

## 4. Data

### 4.1 Prices

- **Source:** end-of-day daily bars, through two sources behind one interface (decided
  2026-10-06, Q3):
  - **Stooq CSV:** no key, stdlib `urllib` + `csv`.
  - **Alpha Vantage `TIME_SERIES_DAILY`:** key in `ALPHAVANTAGE_API_KEY` (in `.env`, which the
    human fills in; never written by an agent, never logged).

  Order: Stooq first, Alpha Vantage when Stooq fails or the user asks for it. The source that
  answered is recorded. Not yfinance (unofficial, against Yahoo's terms, breaks often).
  **Confirm in phase 1** what each source actually serves today, and record the limits in the
  RUN_LOG; free tiers change. In particular, if Alpha Vantage returns only its compact series
  (about 100 bars), indicators that need more history (SMA 200, the 52-week range, 12-month
  return) are reported as `insufficient_history`, never computed on a short window.
- **Window:** about 2 years of daily bars (enough for a 200-day SMA plus a year of history).
- **Cache:** on disk under `web/data/` (gitignored), keyed by ticker + as-of date.
- **Recorded with every fetch:** source, URL (no keys), as-of date, row count, SHA-256 of the
  normalized rows.
- **No new dependency.** If a keyed fallback needs a key, it goes in `.env` / `.env.example`,
  never in a log.

### 4.2 Indicators (`indicators.py`)

Computed in Python, stdlib only, never by the model:

- SMA 20/50/200, EMA 12/26
- RSI 14
- MACD (12, 26, 9)
- ATR 14
- 30- and 90-day realized volatility
- max drawdown over the window
- 52-week high/low and distance from them
- 1/3/6/12-month returns

Each indicator is a named fact `{name, params, value, as_of}` that the model cites by name.
The definitions are written in the docstring with formulas. Tests use hand-checked values on a
small fixture series.

### 4.3 Fundamentals

Reuse the EDGAR facts the layer already fetches (`datasources/edgar.py`) through a
`get_filing_facts` tool, so users don't upload what is already free.

### 4.4 Documents (phase 6)

- Text, Markdown and PDF. PDF needs `pypdf`, a **deliberate exception** documented in
  `documents.py`'s docstring.
- Size and type limits enforced on upload.
- Chunks of about 1,500 characters with stable IDs (`doc_sha256:chunk_n`).
- The agent reads chunks through `read_doc_chunk` / `search_doc`, never as one big block.
- Every chunk is passed to the model inside a fence that says it is untrusted, user-supplied
  content, and that instructions inside it are not to be followed.
- Figures taken from a document are tier "user-asserted". They can be shown and compared, but
  never pass a source check.

### 4.5 The bundle (`bundle.py`)

Freeze the **raw data**, not just the answers the agent got. Agent B in a later verification
will ask different questions of the same data, so the bundle must be able to answer them.

```text
bundle
  bundle_hash         SHA-256 over the canonical JSON below
  created_at, as_of
  prices              {source, as_of, rows[], rows_sha256}
  filing_facts        {cik, accession(s), facts[]}            (the EDGAR snapshot)
  documents[]         {doc_id, sha256, name, chunks[]}         (phase 6)
  tool_calls[]        {seq, agent_slot, tool, args, result, result_sha256}
  inputs_captured     bool: false if the agent used anything outside these tools
```

Stored once per hash in an append-only SQLite table. Records reference it by `bundle_hash`.

## 5. Agents

All three agent kinds use the **same data tools** (`tools.py`): `get_prices`,
`get_indicators(names, params?)`, `get_filing_facts`, `search_doc`, `read_doc_chunk`. Each
call is appended to the bundle's `tool_calls`.

### 5.1 Built-in agent

- Runs on the existing LangChain adapter (Ollama, or Gemini once it is confirmed working; the
  ledger has it unconfirmed). The tools are bound in-process.
- Goes through the existing retry-then-halt loop (ADR-07), with an analysis-specific output
  contract: thought log, conclusion, and a strict JSON signal block.
- The model registry gains `reasoning: "native" | "none"`, which drives D4.
- **Model capability is a known risk.** llama3.2 passed the format check on 2 of 10 attempts
  when it was asked for the extra assessment block (ledger `assessment-not-produced`). Phase 2
  measures the analysis format pass rate per model before the UI depends on it. If a small
  model can't pass, the fix is a separate extraction call (as for B4) or a larger model, never
  coercion.

### 5.2 Session agents (Claude Code, Codex) over `/mcp`

`web/mcp_server.py` already serves sessions for compare runs (`start_compare`, `submit`, ...).
Add analysis tools to the same server:

- `start_analysis(message)`: returns `run_id`, the analysis prompt and the tool list.
- The five data tools above (with `run_id`).
- `submit_analysis(run_id, text)`: goes through the same contract, retry and halt.

The server can't switch off a session's own tools; the user's session configuration does that.
So:

- The instructions tell the agent to use only the layer's data tools, and to say when it used
  anything else (`declared_external_inputs`).
- Any declared external input, or a cited figure that matches no tool result, sets
  `inputs_captured: false`.
- The README gets a recommended session setup: built-in Bash, Write and Edit off; the
  `mycroft` MCP server allowed; a scratch working directory.
- What a session declares is **self-reported**, like its label today (ledger
  `session-identity-self-reported`).

### 5.3 Bring your own agent

- **Path A, MCP client:** the user's agent connects to `/mcp` exactly like 5.2. Its inputs are
  captured.
- **Path B, HTTP endpoint:** the user registers a URL. The layer POSTs
  `{run_id, message, ticker, horizon, tools_url}` and expects back the same text contract (or
  the signal JSON plus cited figures). If the agent didn't use `tools_url`, the run is
  `inputs_captured: false` and every verdict about it says so.
- Phase 5 ships Path A first. Path B ships only with a fixture agent that tests the contract
  end to end.

## 6. The result

### 6.1 Analysis record (`core/analysis.py`)

```text
AnalysisRecord
  run_id, parent_run_id?, created_at
  request     message, ticker, horizon (extracted or asked for)
  agent       kind (builtin | session | byo), model, reasoning (native | none),
              prompt_version, temperature, seed, label (self-reported for session/byo)
  bundle_hash, inputs_captured
  steps[]     the step trace (drives the progress screen)
  attempts[]  every attempt's raw text, kept (ADR-07)
  output      signal (5-point), horizon, conviction (low | medium | high),
              key_points[≤3] {text, evidence_refs[]},
              cited_figures[] {name, value, unit, ref: tool_call seq | chunk_id | "judgment"}
  status      ok | halted | abstained
```

The signal block is strict JSON in a closed vocabulary. An invalid value is a recorded issue,
never coerced (same rule as `core/assessment.py`).

### 6.2 Analysis tab: the signal card

- **Headline:** `B · Buy · 3–6 months · medium conviction`. Every level shows a letter with its
  word (decided 2026-10-06, Q1): A = strong buy, B = buy, C = hold, D = sell, E = strong sell.
  The letter is a display alias of the stored ordinal, so agents are never asked for letters and
  can't return one outside the vocabulary.
- **Horizon:** when the user names none, it is 3–6 months, and the card says "default horizon"
  (decided 2026-10-06, Q2). An explicit horizon in the message overrides it.
- **Why:** up to three reasons, each with evidence chips that say where the claim came from:
  `[Data] RSI 71 ✓`, `[Filing] Q3 revenue $94.9B ✓`, `[Your doc] "guidance raised" p.4 · not
  checked`, `[Judgment]`.
- **Footer:** data as-of date, model and its reasoning mode, a **Verify** button, and a
  verification badge that defaults to **"Not verified."**
- **Disclaimer** on every card: a research signal from a model, not investment advice.

### 6.3 Tier 1: automatic grounding (verification layer, no model)

Runs when every analysis finishes (`validation/analysis_checks.py`):

- Each cited figure is checked against the tool result it references, with the existing
  rounding-aware comparison (`core/numeric.py`).
- A figure with no reference, or one that matches nothing in the bundle, is `UNREFERENCED`.
- Document figures are `USER_ASSERTED`, which is shown but never a pass.
- The result (`grounded: n/m`, plus a list of mismatches) goes on the card's evidence chips.

Tier 1 checks whether the agent quoted its data correctly. Whether the data itself adds up
(recomputing indicators from the raw bars, sanity checks on the bars) is a separate check,
added in phase 7.

### 6.4 Tier 2: on-demand cross-agent verdict (Verification tab)

Pick a run. A = the original agent (kept), B = the chosen agent, or "same model ×N". B runs on
the **frozen bundle**: `replay.py` answers its tool calls from the bundle's raw data, so it can
ask for different indicators or periods than A did. A request the bundle can't answer is
returned as a tool error and recorded, never fetched fresh.

| Verdict | Condition | Shown as |
|---|---|---|
| `CONSISTENT` | both grounded, same signal level | green ✓ "Agreed" |
| `ADJACENT` | both grounded, one level apart | neutral "1 step apart", with the pair's noise floor once measured |
| `DIVERGENT` | both grounded, two or more levels apart, or opposite directions | amber "Disagreed", with the class from `validation/divergence.py` (data / assumption / weighting) |
| `UNGROUNDED` | any agent cited a figure that contradicts the bundle | **red, whether or not they agree** |
| `INCONCLUSIVE` | halted, abstained, no overlap, or `inputs_captured: false` on either side | gray, with the reason |

Order of precedence: `UNGROUNDED` > `INCONCLUSIVE` > the agreement rows. Every state has a text
label and an icon, never color alone.

The verdict is an append-only `VerdictRecord {verdict_id, run_id, bundle_hash, agents[],
tier1[], signals[], distance, divergence_class, verdict, reasons[]}`. The Analysis card's badge
reads the latest one. The verdict enum is the hook for later upstream behavior (for example
`UNGROUNDED` → retry with a corrective directive), which is **not built in this plan**.

The existing decision gate isn't removed. A reviewer can open a gate item on a `DIVERGENT` or
`UNGROUNDED` verdict in the Verification tab through the existing decisions API. No verdict
opens a gate item by itself; a reviewer has to ask (decided 2026-10-06, Q4). An analysis is
never blocked from its own user by the gate (D2).

### 6.5 Noise floor

"Same model ×N" (default N=3) on one bundle records how often a model disagrees with itself.
The Verification tab shows that rate next to `ADJACENT` and `DIVERGENT` verdicts once a pair
has data. Until then it says "noise floor not measured."

## 7. Web app

**Routes** (all localhost-only, like the rest of the server):

- `POST /api/analysis/stream`: SSE stream of steps, then the record.
- `GET /api/analysis/{id}`: the stored record.
- `GET /api/analysis`: list.
- `POST /api/analysis/{id}/verify`: `{agent_b, mode: "pair" | "self", n?}`, streamed.
- `GET /api/analysis/{id}/verdicts`: verdicts for one analysis.
- `POST /api/documents`: upload (phase 6).

**Analysis tab** (`#/analysis`): an LLM-chat layout.

- Conversation in the center and a composer at the bottom with a model picker (each model shows
  its reasoning mode) and an attach button (phase 6).
- Each signal-producing reply is a run card (6.2). Follow-ups that produce a new signal create
  child runs.
- **Progress screen while a run works:**
  - the step trace as a quiet checklist with real values;
  - for native-reasoning models, a collapsible gray "Thinking" section;
  - for others, gray "Working notes" (the thought log, condensed);
  - for session agents, the tool calls.
  - A retry is shown as a retry ("Attempt 2: the reply broke the format"), never hidden.

**Verification tab:** the existing review app pages, plus:

- an analysis list with **Verify**;
- an agent B picker;
- the verdict view: Tier-1 tables for A and B side by side, signals, distance, the divergence
  class, the full thought logs and tool calls, and the bundle summary.

## 8. Security and safety

- **Localhost only, unchanged.** The open-token issue (ledger `audit-criticals`) still applies.
  Don't expose this.
- **Uploads:** size cap, extension and magic-byte check, stored under `web/data/` (gitignored),
  never executed.
- **Prompt injection:** the document fence (4.4), the session setup (5.2), and test cases (9).
- **No investment advice:** the disclaimer on every card. No position sizing and no amounts
  (D9).

## 9. Tests

The suite stays network-free and model-free.

- Recorded price CSV fixtures. Indicator values hand-checked on a small series.
- Bundle hashing is stable across key order. Replay answers recorded and new tool calls from
  raw data, and refuses what the bundle can't answer.
- Fixture agents (scripted replies) for the built-in, session (the real MCP client against
  uvicorn, as `tests/test_mcp_sessions.py` does) and BYO HTTP paths.
- Tier 1: matched, mismatched, unreferenced and user-asserted figures.
- Tier 2: one fixture per verdict row, plus the precedence rules.
- **Planted-errors extension** (`scripts/planted_errors.py`):
  - altered indicator citations (+1%, +10%, a digit swap);
  - a mismatched as-of date;
  - **a poisoned document** whose embedded instruction makes both scripted agents agree. Tier 1
    must still flag it, since document figures never ground.
- Layering test updated. A docs entry for every new file. Frontend Vitest for the card, the
  progress states and the verdict view.

## 10. Phases

Each phase ends with a RUN_LOG entry (and a `divij/work.md` entry for Claude-session work), a
green suite, and a docs build with 0 problems.

1. **Schema and data.** `core/analysis.py`, `prices.py` (source confirmed live, limits
   recorded), `indicators.py`, `bundle.py`, `tools.py`, `replay.py`, DB tables, layering entry.
   *Done when:* tests pass, and one real fetch for AAPL is recorded with its as-of date.
2. **Built-in agent + Tier 1.** `agent.py`, `prompts.py`, the analysis contract, the stream
   route, `analysis_checks.py`.
   *Done when:* tests pass, and the format pass rate is **measured** on ≥10 live runs per model
   tried, with the numbers in the RUN_LOG.
3. **Analysis tab.** Chat layout, run cards, progress screen, signal card, reasoning labels.
   *Done when:* Vitest passes and a live run is watched end to end in the browser.
4. **Tier 2 + Verification tab.** Verify route, the replay-driven agent B, the verdict, the
   noise-floor mode, the verdict view.
   *Done when:* one live pair verdict and one live self-run set are recorded.
5. **Session agents.** The MCP analysis tools, session instructions, `inputs_captured`, the
   README setup.
   *Done when:* a **real** Claude Code or Codex session completes an analysis. A scripted
   client doesn't count (the 2026-10-05 MCP entry's distinction).
6. **Documents.** Upload, chunking, fencing, doc tools, user-asserted tier, the poisoned-document
   planted cases.
7. **BYO HTTP agent + data sanity checks.** Path B with a fixture agent; indicators recomputed
   from raw bars as a filing-style check.

## 11. Explicitly not claimed by this plan

- A `CONSISTENT` verdict means two agents agreed on grounded figures, **not** that the signal
  is right. Agreement isn't proof, and correlated errors are expected.
- Not investment advice, not real-time, no intraday data, no position sizing.
- The agent doesn't find documents itself (a later update).
- No automatic upstream correction on a failed verdict; the enum is only the hook.
- Session and BYO agents' labels and declared inputs are self-reported.

## 12. Questions (all answered by the human, 2026-10-06)

- **Q1.** Show a letter alias next to the five signal levels? **Yes:** A–E (6.2).
- **Q2.** Default horizon when the user names none? **3–6 months**, labelled as the default
  (6.2).
- **Q3.** Which keyed price fallback? **Alpha Vantage**, `ALPHAVANTAGE_API_KEY` (4.1).
- **Q4.** Should a `DIVERGENT` verdict open a gate item automatically? **No**, only when a
  reviewer asks (6.4).

## 13. Changes made while building (append-only)

### Phase 1, 2026-10-06

- **Stooq dropped; Alpha Vantage is the only price source (changes 4.1).** Stooq's CSV endpoint
  answered with a JavaScript proof-of-work browser check instead of data. Getting past that
  would mean defeating a bot check, so it isn't attempted.
- **A free Alpha Vantage key gives 100 daily bars, not two years (changes 4.1, 4.2).** It also
  allows 25 requests a day and 1 a second; `outputsize=full` is premium. The weekly series is
  free with full history. So one fetch is two requests:
  - daily compact, for the short windows;
  - weekly (three years kept), for the long ones: a 40-week average (named as such, not as a
    "200-day"), the 52-week range, 6- and 12-month returns, the 52-week drawdown.

  `sma(period=200)` on daily bars is refused as `insufficient_history`.
- **Splits (new).** Both series are unadjusted; the adjusted daily series is premium. A
  close-to-close move beyond 60% or 160% is recorded as a possible split, and any indicator
  whose bars cross one refuses (`possible_split`) instead of computing across it.
- **Tool calls live in the run record, not the bundle (changes 4.5).** The bundle is the raw
  data only, and its hash covers only that, so the same data is the same bundle and agent B can
  ask its own questions of it. Each recorded call carries its result's hash, and
  `replay.check_recorded_calls` recomputes every call from the bundle.
- **`financial_agent/` may also import `datasources/` (changes 3),** to reuse EDGAR's
  period-aware fact selection instead of copying it.
- **Stale filing facts (new).** Found live: Apple's operating cash flow came back as fiscal Q1
  (ending 2025-12-27) while the rest of the 10-Q ended 2026-06-27. `select_fact` prefers a
  standalone quarter to a year-to-date figure, and cash flow is only reported year to date
  after Q1. The rule is shared with compare runs and isn't changed. Instead, facts older than
  the newest period are marked `stale` and listed by `describe_data`. Open: whether the agent
  should get the latest year-to-date figure instead.
- **The document tools move to phase 6** with uploads, instead of shipping as stubs in phase 1.
- **The `analysis_verdicts` table was built in phase 1,** with the other two tables. Writing
  verdicts is still phase 4.
