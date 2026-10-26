---
title: Financial agent
slug: files-financial-agent
section: Files
order: 75
summary: The user-facing financial agent, a separate component that the verification layer checks. Phase 1 built - prices, indicators, frozen bundles, tools, replay.
---

`financial_agent/` is the user-facing financial agent: a chat interface where a user asks for a
stock signal, and the agent gathers end-of-day prices, computed indicators, SEC filing facts and
(later) uploaded documents through recorded tools, then gives a signal with its reasons. It is a
separate component from the verification layer, and exists so the layer can be tested against a
real agent on real data instead of planted errors and weak agents.

The two components share only the record schema in [[core/analysis.py]]. The agent writes
analysis records; the verification layer reads them and writes verdicts.
[[tests/test_layering.py]] enforces the boundary: `financial_agent/` may import `core`,
`adapters`, `pipeline` and `datasources`, never `validation` or `web`, and `validation/` may not
import `financial_agent/`. The folder sits inside this directory for now so it syncs with the
subtree, and is expected to move out in a later update.

**Built so far: phase 1 of [[financial_agent/PLAN.md]]** (2026-10-06): data, the frozen bundle,
the tools and replay. **Phase 2** (2026-10-26): the prompt, the three-block reply contract, the agent
that runs a model through the retry-then-halt loop, the routes and Analysis page that use it, Tier-1
grounding ([[validation/analysis_checks.py]]) and a script that measures a model's format compliance
live. Tested with scripted models and recorded data, and measured live on one local model; what that
measurement found is in `logs/RUN_LOG.md`. Not built: verification verdicts, session agents,
documents.

## How the files fit together

1. [[financial_agent/prices.py]] fetches two Alpha Vantage series for a ticker (100 daily bars,
   three years of weekly bars) into a price snapshot, cached for the day.
2. [[financial_agent/bundle.py]] freezes that snapshot and a selection of EDGAR facts into a
   bundle whose hash covers the data only.
3. [[financial_agent/tools.py]] is the only way an agent sees the bundle: `describe_data`,
   `get_prices`, `get_indicators` and `get_filing_facts`, each a pure function of the bundle and
   the arguments. `ToolRecorder` numbers and keeps every call with its result's hash.
4. [[financial_agent/indicators.py]] computes the indicators those tools return. It refuses
   rather than computing on too little history or across a possible split.
5. [[financial_agent/replay.py]] checks a stored bundle's hash, reruns recorded calls against it,
   and gives agent B a recorder over the same data.

6. [[financial_agent/prompts.py]] is what the agent is told and the reply format, versioned.
7. [[financial_agent/agent.py]] turns a message into a request, a bundle and a model run, and
   returns the analysis record. [[web/server.py]] serves it at `/api/analysis/*`.

[[web/db.py]] stores bundles, analysis runs and verdicts in three append-only tables.

## `financial_agent/PLAN.md`

**Role:** the build plan for the financial agent and for how the verification layer checks it.

It records the decisions from the 2026-10-06 design review (D1–D14): one user type; the
progress screen driven by the step trace; reasoning labelled per model; frozen input bundles;
a 5-point signal vocabulary instead of AAA–CCC; uploaded documents treated as untrusted and
user-asserted; session agents (Claude Code, Codex) over the existing `/mcp` server limited to
the layer's data tools; and bring-your-own agents under a published contract.

It then specifies:

- the folder layout and the layering rule;
- the price source and indicator set;
- the bundle and replay design;
- the analysis and verdict record shapes;
- the signal card;
- Tier-1 grounding (automatic, no model) and the Tier-2 cross-agent verdict
  (`CONSISTENT`, `ADJACENT`, `DIVERGENT`, `UNGROUNDED`, `INCONCLUSIVE`);
- the web routes and tabs;
- security, tests, seven phases each with a "done when" line, what the plan explicitly does
  not claim, and the four questions the human answered on 2026-10-06 (letter alias A–E,
  default horizon 3–6 months, Alpha Vantage as the keyed price source, no automatic gate item).

Its section 13 records what phase 1 changed from the plan: Stooq dropped, the long-window
indicators moved to the weekly series, tool calls kept in the run record instead of the bundle,
and stale filing facts flagged.

## `financial_agent/__init__.py`

**Role:** package marker for `financial_agent/`. Its docstring states the import boundary.

## `financial_agent/prices.py`

**Role:** end-of-day prices from Alpha Vantage, and a network egress point that
[[tests/test_layering.py]] names.

`fetch_prices(ticker, *, api_key=None, fetch_json=None, sleep=time.sleep, now=None, cache_dir=...)`
returns one price snapshot:

- `ticker`, `source` (`"alphavantage"`), `adjusted` (always `false`), `fetched_at`, and `as_of`
  (the last daily bar's date);
- `requests`: both URLs, with `apikey=REDACTED`;
- `series.daily` and `series.weekly`, each with `rows` (`[date, open, high, low, close, volume]`,
  oldest first), `rows_sha256` and `suspect_dates`.

It makes two requests, `TIME_SERIES_DAILY` (compact) and `TIME_SERIES_WEEKLY`, with a 1.2 s pause
between them. The weekly series is trimmed to `WEEKLY_KEEP = 156` bars.

### What a free key gets (observed 2026-10-06)

- `outputsize=full` is premium: the reply is an `Information` message, not data. Compact gives
  100 daily bars.
- The weekly series is free with full history (1,405 AAPL bars back to 1999).
- 25 requests a day, 1 a second. Past either limit the API still answers HTTP 200, with an
  `Information` message.

Stooq, the plan's first choice, answered with a JavaScript proof-of-work browser check instead of
CSV. Getting past it would mean defeating a bot check, so it was dropped.

### Guarantees

- **Refusals are errors with a kind,** never an empty series: `no_key`, `bad_ticker`,
  `rate_limited`, `premium`, `bad_symbol`, `network`, `bad_response`. A refused daily request
  stops before the weekly one, so a doomed fetch doesn't spend the daily budget.
- **The key is never kept.** Recorded URLs carry `REDACTED`, and error text is scrubbed before it
  is raised.
- **A ticker is checked before it reaches a URL** (`TICKER_RE`: up to 7 characters plus an
  optional class suffix such as `.B`).
- **Bars are sanity-checked.** A non-positive price, or a low above the high, is a
  `bad_response`.
- **Possible splits are recorded.** The series are unadjusted, so a close-to-close move under 60%
  or over 160% of the previous close (`SPLIT_DOWN`, `SPLIT_UP`) puts that date in
  `suspect_dates`.

### The cache

One file per ticker per UTC day, `web/data/price_cache/{TICKER}_{YYYY-MM-DD}.json` (gitignored).
A fetch before the US close reuses the previous close until the next UTC day. A failed fetch
caches nothing.

`python -m financial_agent.prices AAPL` makes one live fetch (or a cache read), and prints the
series and the standard indicator panel.

### Limits and open issues
- Only 100 daily bars, so no 200-day average. The long windows use the weekly series instead.
- No split adjustment: an indicator across a suspect date refuses rather than adjusting. Alpha
  Vantage's adjusted daily series is premium.
- The split thresholds also flag real moves that large (a crash, a buyout).

## `financial_agent/indicators.py`

**Role:** technical indicators computed in code from a price snapshot, never by the model.

`compute(snapshot, name, params)` returns `{name, params, series, unit, as_of, status, ...}`, with
`values` when `status` is `"ok"`. `INDICATORS`: `last_close`, `sma`, `ema`, `rsi`, `macd`, `atr`,
`volatility`, `return`, `range`, `max_drawdown`. Every one takes `params.series`, `"daily"` (the
default) or `"weekly"`. The module docstring gives each formula: Wilder smoothing for RSI and ATR,
an SMA-seeded EMA, the sample standard deviation of log returns annualized by √252 or √52.

- **No value without enough history.** Too few bars returns `insufficient_history` with
  `needed` and `have`. On a free key's 100 daily bars, `sma(period=200)` is refused, not
  computed on 100.
- **No value across a possible split.** If the bars an indicator uses cross a `suspect_dates`
  entry, it returns `possible_split` with the date. The EMA family and the Wilder averages use
  the whole series, so any split in it counts.
- **Malformed requests raise `IndicatorError`:** an unknown name or parameter, a non-integer or
  out-of-range value, `fast >= slow` for MACD.

`STANDARD_PANEL` is the set `get_indicators` returns when nothing specific is asked. It has 17
entries, with the long windows on the weekly series (a 40-week average, the 52-week range, 6- and
12-month returns, the 52-week drawdown). Values are rounded to 4 decimals so that a snapshot
hashes the same everywhere. `describe(result)` gives one line for a trace or a prompt.

## `financial_agent/bundle.py`

**Role:** freezes the data one analysis may see, and hashes it.

`build_bundle(*, ticker, prices, filing=None, documents=None, created_at=None)` returns
`{bundle_hash, created_at, data}`. The hash is [[core/analysis.py]]'s `content_hash` over `data`
only, so the same data built twice is the same bundle. It refuses a price or filing snapshot for
a different ticker. NaN can't enter a bundle, because canonical JSON refuses it.

`bundle_problems(bundle)` lists why a stored bundle can't be trusted: an unknown schema, a hash
mismatch, or price rows that don't match their own `rows_sha256`.

`filing_snapshot(ticker, facts, cik)` selects `FILING_CONCEPTS` (10 us-gaap concepts) from a
companyfacts payload with [[datasources/edgar.py]]'s `select_fact`, keeping each `Fact`'s full
metadata. `fetch_filing_snapshot` is the live path.

**Stale facts (found live, 2026-10-06):** `select_fact` prefers a standalone quarter to a
year-to-date figure. Apple reports operating cash flow only year-to-date in its 10-Qs, so the
selection was fiscal Q1 (ending 2025-12-27), while the rest of the 10-Q ended 2026-06-27. Every
fact ending before the newest period is marked `stale: true` and listed in `stale`, and
`describe_data` reports the list as `older_than_latest_period`. The selection rule itself is
shared with the compare runs and isn't changed here.

## `financial_agent/tools.py`

**Role:** the data tools, the only way an analysis agent sees data.

`TOOL_SPECS` describes `describe_data`, `get_prices` (`series`, `last` 1–260, default 30),
`get_indicators` (optional `requests`, at most 30; otherwise the standard panel) and
`get_filing_facts` (optional `concepts`). `run_tool(data, tool, args)` never raises for a bad
request: an unknown tool, an unknown argument or an out-of-range value returns
`{"error": ...}`, which is recorded like any other result. Nothing here touches the network.

`ToolRecorder(bundle)` keeps one run's calls: `call(slot, tool, args)` appends
`{seq, slot, tool, args, result, result_sha256}`. `seq` runs 1, 2, 3 ... across all slots,
under a lock, because two agents may call at once.

The document tools (`search_doc`, `read_doc_chunk`) come with uploads in phase 6.

## `financial_agent/replay.py`

**Role:** answers tool calls from a stored bundle, and checks recorded calls against it.

- `load_bundle(stored)` raises `ReplayError` naming every problem `bundle_problems` finds.
- `replay_recorder(stored)` returns a fresh `ToolRecorder` over a verified bundle: what agent B
  calls in a Tier-2 verification. It can ask different questions of the same data.
- `check_recorded_calls(stored, calls)` reruns each recorded call and returns
  `{seq, tool, problem}` for:
  - a gap in the numbering;
  - a result that doesn't match its own hash;
  - a result the bundle no longer gives, even when the record's hash was updated to match.

  An empty list means every result is reproducible from the data.

Related: [[tests/test_fa_bundle.py]]

## `financial_agent/prompts.py`

**Role:** what the financial agent is told, as a versioned constant, and the corrective text for a
retry.

`ANALYSIS_PROMPT_VERSION` (`analysis-v1`) and `ANALYSIS_DIRECTIVE` travel together: a stored record
names the version it ran under, so it never points at words that were edited later. The directive
asks for exactly three blocks, `<thought_log>`, `<conclusion>` and `<signal>`, each tag on its own line,
and says:

- the only source of fact is the data tools, in a stated order (describe_data, get_indicators,
  get_filing_facts), called through the tool interface; a reply written without calling any tool is
  rejected;
- every tool result starts with its call number, and a figure is cited by that number;
- the signal is one JSON object in the closed vocabulary of [[core/analysis.py]] (signal, horizon,
  conviction, up to three key points with evidence, cited figures);
- no investment advice, no amounts, no position sizes.

The current version is `analysis-v2`. **v1 was a long, careful directive and it failed live**: on
2026-10-26 it made `llama3.2` skip the tools altogether, writing a reply that narrated tool calls and
invented their results (a probe with a short prompt made the same model call the tool in 3 seconds,
and a larger context window changed nothing). v2 is short and puts the order of work first. The
record of both is in `logs/RUN_LOG.md`.

`user_prompt(request, bundle)` adds the request, ticker, horizon (labelled as the default when the
user named none), the data's as-of date and the bundle hash. `corrective_text(reason)` is added on
the second attempt and names what was wrong with the first reply.

### Limits and open issues
- How well a real model follows this directive is measured by [[scripts/measure_analysis_format.py]],
  and only for the models and dates in its report; the results are in `logs/RUN_LOG.md` and the
  Analysis page states them. The ledger already records a small model passing a similar extra block
  2 times in 10 (`assessment-not-produced`).
- A small model still copies a template literally, or mimics what it saw in a tool result, when it can.
- The directive's worked example sits in the directive text, so the echo check can reject a reply
  that copies eight words of it.

Related: [[financial_agent/agent.py]], [[core/analysis.py]]

## `financial_agent/agent.py`

**Role:** the built-in financial agent: a request in, an analysis record out.

What it does, in order:

1. `plan_request(message)` finds the ticker with [[datasources/tickers.py]] (a symbol, or a
   company name with a capital letter) and the horizon (`extract_horizon`: "6 months", "2 weeks",
   "long-term", else the default `3-6m`, flagged as defaulted). It raises `RequestError` saying
   what to change when no company is named.
2. `prepare_bundle(ticker)` fetches prices (required: a `PriceFetchError` says why not) and EDGAR
   filing facts (optional: if they can't be had the bundle has none and a note says why), and
   freezes them with [[financial_agent/bundle.py]].
3. `run_analysis(request, bundle=..., model_step=..., model=...)` builds the adapter and calls
   `run_validation_loop` from [[pipeline/middleware.py]], so a broken reply gets one corrective
   retry and a second breaks the run. It returns an `AnalysisRecord`: status `ok` with the signal,
   or `halted` with every attempt's raw text kept and no signal.

The model is injected as a `ModelStep`, a function from the conversation to the model's next
turn, so tests drive the whole path with scripted turns. The adapter lets the model call the data
tools for up to `MAX_TOOL_ROUNDS` (8) turns per attempt through a `ToolRecorder`, so every call is
numbered and hashed. A tool result is shown to the model as `call n · tool` on a first line and the
result as plain JSON after it, not as an object holding the result: with `{"call", "tool", "result"}`
a small model copied that shape into the arguments of its own calls.
`parse_analysis_response` reads the three blocks and requires the signal JSON to parse as written:
a fenced or commented block fails, so the pass rate measures what the model did. A signal outside
the vocabulary fails the reply and is never coerced. A reply written without making any data call fails too (found on the first measured run, when a reply
passed the format with no tool call and a thought log full of invented results); its raw text is kept.
A reply that repeats the directive fails too:
the loop only checks that against the retry's generic corrective text, so the adapter checks it
against the real directive (found by a test on 2026-10-26).

`langchain_model_step` is the real model: LangChain over Ollama or Gemini with the data tools bound
(`tool_schemas`, built from `TOOL_SPECS`), connection and stall errors raised as
`LangchainConnectionError` like every other call in the layer.

### Design notes
- `inputs_captured` is `True` here because every figure the agent can use came through the
  recorded tools. It says nothing about what the model remembers from training.
- `reasoning_mode(model)` is `native` only for names in a short list, otherwise `none`: its notes
  are shown as working notes, never as thinking (PLAN.md D4). Extend the list from measurement.
- The record carries `data_as_of`, `corrective_text`, `issues` (what the signal check dropped or
  filled in) and `halt_reason`, beyond the shape in the plan.

### Limits and open issues
- Nothing here checks that the signal is right. Whether its figures match the tools' results is
  [[validation/analysis_checks.py]] (Tier 1), which the route runs when the analysis finishes: the
  agent package doesn't import `validation/`. Whether the signal follows from the figures is not
  checked by anything yet.
- A model that can't be reached raises and nothing is recorded, as in the compare routes. A run
  that fails that way leaves no trace of its steps.
- A company named in lower case ("nvidia") isn't found.
- `load_measurements()` and `measurement_for()` read [[financial_agent/model_measurements.json]] so
  the page can say how a model has done, or that it was never measured; nothing is guessed.
- The unit and integration tests use scripted models and recorded Alpha Vantage and EDGAR payloads.
  What a real model does is in the measurement, not in the tests.

Related: [[financial_agent/prompts.py]], [[financial_agent/tools.py]], [[tests/test_fa_agent.py]],
[[web/server.py]]

## `financial_agent/model_measurements.json`

**Role:** the compact per-model result of the last live format measurement, read by the Analysis page.

Written by [[scripts/measure_analysis_format.py]] (after every run, so a partial measurement is a
valid file): when it was measured, the prompt version and temperature, the tickers, and per model the
runs completed, how many first replies passed the format, how many were rescued by the retry, how
many halted, and the two rates. `load_measurements()` in [[financial_agent/agent.py]] reads it and
treats a missing, unreadable or oddly shaped file as "not measured". The page says whether a
measurement was made under the prompt version in use now.

### Limits and open issues
- It is a count of live runs by one model on the tickers named, not a guarantee, and it is
  overwritten by the next measurement.
- It is generated; editing it by hand would make the page state something nobody measured.

Related: [[scripts/measure_analysis_format.py]], [[web/server.py]]
