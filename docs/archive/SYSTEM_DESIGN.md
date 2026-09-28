# System Design — Accountability Layer & Cross-Agent Validation

**Scope of this document:** the two mechanisms this subsystem implements — the accountability
layer (verifies one agent's own output) and Cross-Agent Validation (compares two agents against
each other). It describes system workflow, data model, and the design decisions behind both,
end to end, as the code actually stands today.

**Status discipline (read this before anything else in the document):** per this subsystem's own
rule against overclaiming, every claim below is either (a) something the code does, verified by a
passing test, or (b) explicitly marked as observed only in a specific, cited live run, or (c)
explicitly marked as not yet done. Nothing here should be read as "this is production-ready" — see
§7. Cross-checked against `divij/cross-agent-validation-proposal.md` §9 and `divij/sdd.md` §14
(the two documents that exist specifically so this kind of claim doesn't have to be re-derived or
assumed) before writing this.

**As of:** 2026-09-14. **242 automated tests passing** (`GET /api/self-report` is the live source
of truth for this number — it is discovered, not hard-coded, so it cannot drift from this
document). **Nothing in this subsystem is committed to git** past commit `c53746a` — see
`logs/RUN_LOG.md`'s 2026-09-04 through 2026-09-11 entries.

---

## 1. What these two layers are, and how they relate

| | Accountability layer | Cross-Agent Validation |
|---|---|---|
| **Question it answers** | "Did this one agent's output survive structural verification?" | "Do two independent agents' conclusions about the same subject contain a numeric contradiction?" |
| **Unit of evidence** | One `ReasoningObject` per attempt, one `RunSession` per run | Two full `RunSession`s (one per agent) sharing one `run_id`, plus one `CrossAgentComparisonResult` |
| **Core mechanism** | `pipeline/middleware.py::run_validation_loop` — call, parse, retry once, halt | `validation/cross_validation.py::run_cross_agent_validation` — run the *same* validation loop twice, independently, then diff the two conclusions |
| **New code vs. reused code** | The foundation | Reuses the validation loop, the schemas, and the persistence layer **unmodified** — see §4.1 |

Cross-Agent Validation is not a parallel system. It is one additional function that calls the
accountability layer's own `run_validation_loop` twice and adds a comparison step on top. Every
guarantee the accountability layer makes (structural parsing, the one-retry-then-halt rule,
append-only storage, scope-based redaction) applies identically to each of the two agents it runs.

---

## 2. System workflow — accountability layer

### 2.1 End-to-end request flow (`POST /api/chat`)

```
client
  │  POST /api/chat  { message, scope, provider?, model? }
  ▼
web/server.py: require_scope() ─── validates JWT, returns "auditor" | "investor"
  │
  ▼
adapters/registry.py: build_adapter(config) ─── selects gemini | ollama | mock by config
  │
  ▼
pipeline/middleware.py: run_validation_loop(subject, context, run_id, agent_id, call_agent_fn=adapter)
  │
  ├─ Attempt 1: adapter(subject, context, ACTIVE_DIRECTIVE)
  │     │
  │     ├─ raw text parses (core/parsing.py::_parse_response) ──► ReasoningObject(attempt=1, SUCCESS)
  │     │                                                          └─► done, return AgentResponse
  │     │
  │     └─ StructuralParseError ──► ReasoningObject(attempt=1, PARSE_FAILURE)
  │           │
  │           ▼
  │        Attempt 2: adapter(subject, context, CORRECTIVE_DIRECTIVE)
  │           │
  │           ├─ parses ──► ReasoningObject(attempt=2, SUCCESS) ──► done
  │           │
  │           └─ StructuralParseError again ──► ReasoningObject(attempt=2, HALT)
  │                 └─► raise HaltError(reasoning_objects=[...]) — no conclusion delivered
  │
  ▼
web/server.py: build RunSession, serialize (ReasoningObject.to_dict(investor_scope=...))
  │
  ├─ ADR-06 mitigations (if a conclusion exists): extract_claims → verify_claims,
  │  and (Ollama, or opt-in) run_consistency_probe — a second independent call, compared
  │
  ▼
web/db.py: insert_run(payload), insert_session(...) ─── append-only SQLite
  │
  ▼
response to client, redacted per the caller's own scope
```

### 2.2 The structural contract (`core/parsing.py`)

The accountability layer does not trust an LLM's own claim about its reasoning — it enforces a
**structural** contract instead: the entire raw response must be exactly two XML blocks,
`<thought_log>` and `<conclusion>`, with nothing else — no preamble, no markdown fences, no text
after the closing tag. `_parse_response` raises `StructuralParseError` (carrying the raw text) if
either block is missing or if any text exists outside the two blocks. This is a **regex-based
structural check, not a semantic one** — it says nothing about whether the reasoning is correct,
only whether the response is shaped the way the system can process it as evidence.

This is deliberately the *only* thing the parser checks. Semantic correctness is a different,
harder problem (§2.6, §4.4) layered on top, never conflated with structural validity.

### 2.3 ADR-07 — the retry-then-halt loop, exactly

`run_validation_loop` (`pipeline/middleware.py`) is the single place this rule is implemented:

1. **Attempt 1** uses `core/directive.py`'s `ACTIVE_DIRECTIVE` (currently `v1.1.0`).
2. If parsing fails, that failure is recorded as its own `ReasoningObject` (`attempt_number=1`,
   `parse_status=PARSE_FAILURE`) — **the failed attempt is evidence, not noise**, and it is written
   to the record even though no conclusion was produced from it.
3. **Attempt 2** uses a fixed `CORRECTIVE_DIRECTIVE_TEXT` — a short, blunt restatement of the
   format requirement — regardless of what the active directive was on attempt 1.
4. If attempt 2 succeeds, both `ReasoningObject`s are kept (`PARSE_FAILURE` then `SUCCESS`).
5. If attempt 2 also fails, both are kept (`PARSE_FAILURE` then `HALT`), and `HaltError` is raised
   carrying the objects. **The pipeline does not degrade, does not guess, does not pass through a
   best-effort answer.** No conclusion means no conclusion.

`ReasoningObject.__post_init__` enforces this shape as a hard invariant, not just as middleware
behaviour: it raises `ValidationError` if `attempt_number == 2` and `parse_status == PARSE_FAILURE`
— attempt 2 can only ever be recorded as `SUCCESS` or `HALT`. It is structurally impossible to
construct a third attempt or a third outcome.

### 2.4 Directive versioning (ADR-01b, ADR-05)

The directive is the system prompt that instructs the model to produce the two-block format. It is
**hardcoded and versioned in code** (`core/directive.py`), not runtime-configurable — SEC-04 states
plainly that no runtime parameter can modify directive content, closing off a class of prompt-
injection-via-config risk. Two versions exist today:

- **`v1.0.0`** — the initial prototype directive.
- **`v1.1.0`** (active) — adds an explicit "begin with `<` " rule and forbids any pre-reasoning
  text before the opening tag. This was a reaction to a real, observed failure mode: Gemini models
  producing numbered reasoning steps as plain text *before* `<thought_log>`, which the parser's
  boundary check correctly rejected as "text outside the blocks" — the fix was to the directive,
  not to loosen the parser.

Old versions are kept in a registry (`get_directive(version)`), never deleted, so a `RunSession`
recorded against `v1.0.0` remains auditable against the exact text that was active for that run —
`directive_text` is stored **verbatim on every `RunSession`**, not by a version pointer that could
later resolve to different text if a version string were ever reused.

### 2.5 Confidence scoring (ADR-04, ADR-08)

`confidence_score` is a required field on every `ReasoningObject`, constrained to `[0.0, 1.0]` by
`__post_init__`. Two rules matter here:

- It is **computed, not self-reported** — ADR-04's point is that an agent stating its own
  confidence is not evidence of anything; whatever computes this score does so from measurable
  properties of the run, not by asking the model to grade itself.
- `RunSession` cross-checks `run_confidence_score` against `confidence_classification`: below
  `0.4` must be classified `HIGH_UNCERTAINTY_SPECULATIVE`, at or above must be `STANDARD` — set
  the wrong pairing and construction raises `ValidationError`. The threshold is a hard invariant
  in the type, not a convention someone could forget to apply at a call site.

### 2.6 ADR-06 partial mitigations — claims, verification, consistency

Structural validity says nothing about whether a conclusion's *content* is trustworthy. Three
separate, composable mechanisms exist on top of the structural loop, all stdlib-based:

| Module | What it does | What it does NOT do |
|---|---|---|
| `validation/claims.py` | Parses a `thought_log` into typed `ExtractedClaim`s: `citation`, `quantitative`, `hedge`, `causal` — via `core/numeric.py`'s shared regex plus pattern lists for hedge words and causal connectors. | Does not judge whether a claim is *true*. |
| `validation/verification.py` | For each citation claim with a URL, fetches the source (stdlib `urllib` only — a deliberate stdlib-only constraint) and checks whether the thought_log's quantitative claims actually appear in it. Understands SEC EDGAR's companyfacts JSON shape specifically, falls back to a generic text/number search otherwise. Returns `verified: True/False/None` per claim (`None` = source unreachable, not "assumed true"). | Does not verify a claim with no citation attached — an uncited number is invisible to this mechanism by construction. |
| `validation/consistency.py` | Re-runs the *same* query through the *same* agent a second time and scores the two conclusions' word/number overlap (`0.6` weight on numbers, `0.4` on words) into `HIGH`/`MEDIUM`/`LOW`/`UNKNOWN`. The probe run gets a fresh UUID and is **never persisted** — it is metadata on the primary run, not a second audited attempt. | Two consistent confabulations still pass as `HIGH` — consistency is weak positive evidence, strong negative evidence, stated exactly that way in the module's own docstring. |

Since 2026-09-11, `/api/compare` calls `extract_claims`/`verify_claims` too — see §4.5.

### 2.7 Persistence (`web/db.py`) — C-03

SQLite, one file (`web/data/accountability.db`, gitignored). Two design choices carry the whole
append-only guarantee:

- **`BEFORE UPDATE`/`BEFORE DELETE` triggers on the `runs` table** call `RAISE(ABORT, ...)`
  unconditionally. This is enforced by the database engine itself, not by application code
  choosing not to call `UPDATE` — a bug elsewhere in the codebase cannot silently mutate history.
  The only way around it is `clear_all()` (drops and recreates tables — admin/test only) or the
  TTL purge, which explicitly drops and recreates the trigger around a scoped, cutoff-dated delete.
- **The full run payload is stored as one `payload_json` blob**, with `ticker`/`scope`/`status`
  pulled out as real columns purely for indexed querying. `cross_agent_comparison` (§4) lives
  inside that same blob with no schema change — the stated tradeoff (§4.6) is that "every
  contradiction this month" needs an in-process scan (`list_contradictions`) rather than a SQL
  `WHERE` clause, accepted deliberately rather than adding a migration for v1.

A 90-day TTL purge (`purge_old_runs`) runs on startup and on every write.

### 2.8 Scope-based redaction (SEC-01) — and its one known gap

`ReasoningObject.to_dict(investor_scope=...)` is where SEC-01 is actually enforced: at
`investor_scope=True`, `thought_log`, `raw_output`, and `llm_tokens` are **structurally absent
from the dict**, not nulled — a consumer checking `"thought_log" in payload` gets a correct
answer either way, rather than a key present with a `null` value that could be confused with "the
model produced no reasoning."

**The gap, stated plainly:** `RunSession.to_dict()` calls `ro.to_dict(investor_scope=False)`
unconditionally for its nested `reasoning_objects` — the *top-level* list in an HTTP response is
redacted per the caller's real scope (`web/server.py` does this explicitly at both `/api/chat` and
`/api/compare`), but the copy nested inside `session` is always full/auditor-scope. This is
`session-scope-leak` in the ledger (§7) — open, not fixed, present in exactly the same shape in
both routes.

---

## 3. System workflow — Cross-Agent Validation

### 3.1 Why it exists, and what it deliberately is not

Per `divij/cross-agent-validation-proposal.md` (2026-08-21), Mycroft's declared architecture names
Cross-Agent Validation as a capability with **no implementation anywhere in the project** before
this. The v1 scope is narrow on purpose (§9 of that proposal, restated here so this claim doesn't
drift from its source):

- It detects **numeric** disagreement between two conclusions — not reasoning errors.
- It does **not** decide which agent is right.
- It does **not** generalize to non-numeric claims (dates, qualitative statements, causal claims).
- It is explicitly **not** an implementation of "Pattern Recognition" and should never be
  described as one.

### 3.2 The two producers

| | Producer A (`producers/financial.py`) | Producer B (`producers/earnings.py`) |
|---|---|---|
| `AgentID` | `FINANCIAL` | `EARNINGS` |
| Concepts read | `Assets`, `Revenues`, `NetIncomeLoss` | `EarningsPerShareDiluted`, `EarningsPerShareBasic`, `OperatingIncomeLoss` |
| Data source | Same SEC EDGAR companyfacts payload | Same SEC EDGAR companyfacts payload |

Both are one `ConceptLens` value each (`producers/lens.py`), not two hand-written modules — a
third producer is a new `ConceptLens` literal, not a copied file (OCP; see §5). The concept sets
are **deliberately disjoint** by design: the whole premise of a *meaningful* disagreement is that
the two agents saw genuinely different evidence, per the information-asymmetry rationale first
argued in `logs/RUN_LOG.md`'s 2026-08-29 "Comparator semantics for information-asymmetric agents"
entry. `ConceptLens.summarize()` renders each producer's context as literal `Concept: value` lines
— a format contract the UI's input-provenance matcher parses back out, not just a presentation
choice.

### 3.3 The comparison flow (`validation/cross_validation.py::run_cross_agent_validation`)

```
run_cross_agent_validation(subject, context_a, context_b, AgentID.FINANCIAL, AgentID.EARNINGS,
                            adapter_a, adapter_b, run_id=shared_run_id, contradiction_rule=...)
       │
       ├─ run_validation_loop(..., AgentID.FINANCIAL, adapter_a)  ── the UNMODIFIED ADR-07 loop
       │      → conclusion_a, a_objects[]  (or HaltError → a_objects[] carries the halt evidence)
       │
       ├─ run_validation_loop(..., AgentID.EARNINGS, adapter_b)   ── independently, regardless of A's outcome
       │      → conclusion_b, b_objects[]
       │
       ├─ status = COMPARED | AGENT_A_HALTED | AGENT_B_HALTED | BOTH_HALTED
       │
       ├─ if COMPARED:
       │      score, word_overlap, number_overlap = _compute_score(conclusion_a, conclusion_b)
       │                                              (reused unmodified from validation/consistency.py)
       │      contradiction_flag, divergent_numbers = <one of two rules — see 3.4>
       │  else:
       │      contradiction_flag = None   ── "no comparison happened", never coerced to False
       │
       └─ return CrossAgentComparisonResult, a_objects + b_objects  (both agents' FULL attempt history)
```

Two invariants worth naming explicitly:

- **Agent B always runs, even if Agent A halted.** There is no reason one agent's structural
  failure should suppress the other's evidence — both partial results are informative.
- **`contradiction_flag` is `None`, never `False`, when `status != COMPARED`.** `False` would
  assert "checked, found no contradiction" — a materially stronger claim than "no comparison was
  possible." This is a direct application of P3 (never invent a count/rate/confidence that wasn't
  actually produced).

### 3.4 Contradiction detection — two rules, one opt-in

`run_cross_agent_validation`'s `contradiction_rule` parameter selects between two independent
mechanisms. The default preserves every pre-2026-09-11 caller and test unchanged; production
(`/api/compare`, `scripts/run_cross_agent_live.py`) uses the second.

**`"symmetric_difference"` (default)** — a number present in exactly one conclusion is
`divergent`. Whether that alone counts as `contradiction_flag=True` depends on
`concepts_expected_to_overlap`:
- `True` (fixture-test default): any asymmetry is itself suspicious — correct when the two agents
  are genuinely answering the same question.
- `False` (used historically for Producer A/B): requires **both** sides to have cited at least one
  number before anything counts as a contradiction — fixes the common "one agent quantifies
  something, the other simply wasn't asked" false-positive shape, but has a real, once-measured
  failure mode: it also suppresses a flag whenever the *other* side happens to have zero numbers,
  which silently swallowed the one confirmed historic fabrication catch (the AAPL 0.34
  debt-to-equity case) until `concept_aware` replaced it in production.

**`"concept_aware"`** (`validation/concept_linkage.py`, production default since 2026-09-11) — a
different mechanism entirely: each extracted number is tagged with the known lens concept nearest
it (by keyword proximity, checking both natural-language phrasing and the raw XBRL tag name a
model may echo verbatim). A number tagged to a *known* concept is **excluded from comparison
entirely** — Producer A/B's vocabularies are disjoint by construction, so such a number could never
be corroborated by the other side regardless of what it says. Only **untagged** numbers (a bare
ratio/percentage with no recognizable concept nearby — the shape a fabricated figure or a
legitimate derived metric both take) keep the old presence/absence rule. Measured against every
real run ever persisted (`tests/fixtures/cross_agent_real_runs_corpus.json`, 31 runs): kills 15 of
16 disjoint-concept false positives, preserves the one confirmed true positive, introduces zero new
false positives (`tests/test_concept_linkage.py`, `tests/test_real_run_corpus.py`). It does **not**
solve the untagged-ratio ambiguity — a legitimate derived ratio and a fabricated one are still
indistinguishable to this mechanism alone (§7).

### 3.5 Numeric extraction — one regex, shared everywhere (`core/numeric.py`)

`QUANTITATIVE_RE` is the single definition of "a number" for `claims.py`, `consistency.py`,
`verification.py`, and (via `consistency.extract_numbers`) `cross_validation.py`. It used to be
three hand-copied regexes with a comment asking the reader to keep them in sync — consolidated
specifically because a drifted copy is a P3 violation that raises no error (one module verifies a
claim the extraction step never produced, or the comparator calls two conclusions "identical" over
a number one of them cited and the other didn't). Alternatives, in match order: `$`-prefixed,
`%`-suffixed, `x`-multiple, `bps`, comma-grouped with no prefix/suffix at all (added 2026-09-11 —
`"13,971,000,000.0"` was previously truncating to `"000.0"`, since `\d+` cannot cross a comma),
and a bare-decimal fallback (`0.34`) — the last one is a deliberate, accepted false-positive
tradeoff: it also matches a section number like "2.1", chosen once here rather than reintroducing
the same tradeoff in three files.

### 3.6 The HTTP route (`POST /api/compare`)

```
POST /api/compare  { ticker, agent_a_provider?, agent_a_model?, agent_b_provider?, agent_b_model? }
       │
       ├─ lookup_cik(ticker) ─────────────────── ONE call, traced as a step
       ├─ fetch_company_facts(ticker, cik) ───── ONE call — the SAME payload object goes to BOTH lenses
       │        │
       │        ├─ summarize_facts()          → context_a  (Producer A's lens over the shared payload)
       │        └─ summarize_earnings_facts()  → context_b  (Producer B's lens over the SAME payload)
       │
       ├─ adapter_a, adapter_b built via adapters/registry.py, each wrapped in wrap_adapter()
       │        so every LLM attempt (including ADR-07's retry) is its own timed, labeled step
       │
       ├─ run_cross_agent_validation(..., contradiction_rule="concept_aware")   ── sequential, not concurrent
       │
       ├─ per producer: extract_claims(thought_log) → verify_claims(...)        ── since 2026-09-11
       │        (independently — a claim from A's log is never checked against B's citations)
       │
       ├─ persist_cross_agent_run(result, objects, scope=caller_scope)
       │
       └─ response: cross_agent_comparison, reasoning_objects (redacted per CALLER's real scope —
                     not the scope baked into storage, which is unconditionally full — see §2.8),
                     claims + verification_rate per producer, steps (the full timed trace),
                     contexts (the verbatim strings each producer actually saw)
```

Two things the step trace exists specifically to make visible, because they are easy to assume
wrongly from the UI alone: the fetch happens **once**, not twice (both lenses read one shared
payload), and the two agents run **sequentially**, not concurrently.

---

## 4. Design decisions and rationale

| Decision | Why |
|---|---|
| **Core engine is stdlib-only** (parser, middleware, schemas, directive, numeric extraction) | Anyone can run the test suite on a bare interpreter with zero install step; every dependency risk is scoped to the web layer and the optional LLM providers, which are exactly the parts already flagged as not-production-ready (§7). |
| **Every schema is a frozen dataclass** | Mirrors the append-only store at the type level — a `ReasoningObject` cannot be mutated after construction in Python any more than the `runs` table can be `UPDATE`d in SQLite. Two independent enforcement layers of the same guarantee (P3/ADR-03). |
| **`AgentAdapter` is a `Protocol`, not a base class** (`core/contracts.py`) | Structural typing means `pipeline/middleware.py` depends on a *shape*, not on any adapter's identity — a new provider satisfies the contract by having the right `__call__` signature, no inheritance required, no adapter needs to know the loop exists. |
| **The directive is hardcoded in versioned code, not runtime-configurable** | SEC-04: closes off "modify the system prompt via a request parameter" as an entire class of risk, and makes every `RunSession`'s `directive_text` a fact about what deployment produced it, not a value that could be spoofed at call time. |
| **Append-only enforced by SQLite triggers, not application discipline** | A bug anywhere else in the codebase — a route, a script, a future contributor — cannot silently rewrite history, because the database itself refuses the statement. Enforcement lives at the layer that's hardest to accidentally bypass. |
| **`cross_agent_comparison` is a new JSON key inside the existing `payload_json` blob, not a new table/column** | Explicit, accepted tradeoff (SDD §7.3): zero migration, zero schema risk, at the cost of "every contradiction this month" needing an in-process scan rather than a SQL `WHERE`. Revisit only if that scan becomes a measured bottleneck — it hasn't been. |
| **Two producers via one `ConceptLens` value type, not two modules** | Before this (2026-09-04 SOLID restructure), `financial_grader.py`/`earnings_grader.py` were near-identical files down to a verbatim-copied `_latest_value` helper — a third producer meant copying a third module. Now a third producer is a `ConceptLens` literal; the runner (`run_lens`), and in particular ADR-07's retry/halt behaviour, cannot drift between producers because there is exactly one call site for it. |
| **Producer A/B's concept sets are deliberately disjoint** | The stated rationale (logs/RUN_LOG.md, 2026-08-29): meaningful disagreement requires genuinely different evidence. The measured cost of this choice is the entire subject of §3.4/§7 — disjoint vocabularies make it structurally likely that any two numbers differ *because they're about different things*, not because the agents disagree, and nothing about "two agents, deliberately shown different evidence" can ever exercise a "same fact, different value" disagreement — see the honest note in §7. |
| **`contradiction_rule` is an opt-in parameter with a compatibility-preserving default** | Introducing `"concept_aware"` (2026-09-11) needed to not silently change any of the ~15 existing tests asserting the old rule's exact behaviour. Every caller that doesn't pass the parameter is byte-for-byte unaffected — verified by running the full suite before writing a single new test for the new mode. |
| **Both halted and successful runs persist full evidence** | A halt is not an absence of evidence — `HaltError` carries every `ReasoningObject` written so far specifically so the caller persists them. "The agent failed structural validation twice" is itself an audited fact. |
| **Investor vs. auditor scope is enforced by *omitting* dict keys, not nulling them** | A consumer can distinguish "this field doesn't exist at your scope" from "the model produced no output here" — nulling would conflate the two. |
| **The consistency probe's second run is never persisted** | It exists purely as a self-check on the primary run (`validation/consistency.py`'s own docstring: "the probe run is NOT persisted... it is metadata on the primary run") — persisting it would double the audit surface for a run whose only purpose is agreement-checking, not itself being evidence. |
| **`verify_claims` returns `None`, not `False`, when a source is unreachable** | Same P3 discipline as `contradiction_flag`: "could not check" and "checked, found false" are different facts, and collapsing them would let a network hiccup silently read as "this claim is wrong." |

---

## 5. Data model reference

| Type | Module | Key fields | Notes |
|---|---|---|---|
| `DataSource` | `core/schemas.py` | `source`, `status` (`live`/`simulated`/`failed`/`cached`), `url`, `provenance_note` | One per data source an agent actually consulted. |
| `Citation` | `core/schemas.py` | `label`, `url`, `excerpt` (≤500 chars) | A source cited inside a conclusion. |
| `ReasoningObject` | `core/schemas.py` | `run_id`, `agent_id`, `attempt_number` (1 or 2), `parse_status`, `confidence_score`, `thought_log`*, `conclusion`, `raw_output`*, `llm_tokens`* | Frozen. `*` = investor-scope-excluded fields (SEC-01). One per attempt, including failed ones. |
| `RunSession` | `core/schemas.py` | `ticker`, `directive_version`, `directive_text`, `status`, `reasoning_objects` | One per analysis run. `directive_text` stored verbatim (ADR-05). |
| `AgentResponse` | `core/parsing.py` | `thought_log`, `conclusion`, `raw_text` | The parsed form of one structurally-valid raw response. |
| `ComparisonStatus` | `validation/cross_validation.py` | `COMPARED` / `AGENT_A_HALTED` / `AGENT_B_HALTED` / `BOTH_HALTED` | What kind of comparison outcome this run produced. |
| `CrossAgentComparisonResult` | `validation/cross_validation.py` | `status`, `agent_a/b_conclusion`, `agent_a/b_numbers`, `divergent_numbers`, `contradiction_flag` (`bool \| None`), `score`, `agreement` | Embedded as one new key (`cross_agent_comparison`) inside the same run payload shape `/api/chat` already produces — no schema change (§4). |
| `ExtractedClaim` | `validation/claims.py` | `claim_type` (`citation`/`quantitative`/`hedge`/`causal`), `text`, `context`, `source_url`, `verified` (`bool \| None`) | Produced from a `thought_log`; `verified` stays `None` until `verify_claims` runs. |

---

## 6. Test & verification discipline

- **242 automated tests, stdlib `unittest` only** — no network, no live model call anywhere in
  `python -m unittest discover -s tests -t .`. Every place that would otherwise need one (EDGAR
  fetches, LLM calls) takes an injected fetcher or a fixture/mock adapter instead.
- **A real-run regression corpus** (`tests/fixtures/cross_agent_real_runs_corpus.json`) — every
  cross-agent run ever actually persisted, hand-labeled (with the label itself flagged as an AI
  judgment pending human review, per P8), replayed through today's real code on every test run so
  a behavioural claim about the comparator is a canary, not a one-time assertion.
- **Live-model tests are tracked separately from the automated suite** (`web/self_report.py`'s
  `LIVE_MODEL_TESTS`), each with `expected`/`actual`/`verdict` and an explicit `outcome`
  (`worked` / `gap_found` / `inconclusive`) — six so far, against `qwen2.5:7b` and (as of the
  overlapping-concept test) `mistral-7b`, both via local Ollama.
- **A layering test enforces the package structure by reading the AST**, not by convention alone
  (`tests/test_layering.py`) — checked to actually fail by deliberately introducing violations
  before trusting it.
- **Route-level HTTP tests exist for `/api/compare` as of 2026-09-11** (`tests/test_compare_route.py`)
  — 3 tests, the first automated coverage any route in `web/server.py` has ever had. Every other
  route, and the browser JS, remain untested at that level (§7).

---

## 7. Honest status — what's resolved, what isn't

Live ledger: `GET /api/self-report` / `web/self_report.py::KNOWN_ISSUES`. Snapshot as of this
document (15 entries; 9 open/unverified, 4 resolved, 2 by-design, 1 critical):

| Issue | Severity | Status | One-line summary |
|---|---|---|---|
| `disjoint-concepts` | high | **RESOLVED** | `concept_aware` kills 15/16 real false positives; the untagged-ratio ambiguity is a separate, still-open residual (see below). |
| `asymmetry-fix-suppresses-historic-true-positive` | high | **RESOLVED** | Fixed as a side effect of the above — `concept_aware` never had the both-sides-non-empty gate that caused this. |
| `regex-truncates-comma-grouped-numbers` | medium | **RESOLVED** | `core/numeric.py`'s comma-grouped alternative, 2026-09-11. |
| `fabrication-not-caught` | high | **RESOLVED** | `/api/compare` now calls `extract_claims`/`verify_claims` per producer; confirmed against the real historic case (`verification_rate=0.0`). |
| `audit-criticals` | **critical** | OPEN | Investor redaction bypassed at storage time; 14/16 routes unauthenticated; unauthenticated `DELETE /api/runs`; `POST /api/auth/token` mints any scope for anyone. **Not deployable beyond localhost.** |
| `session-scope-leak` | high | OPEN | The nested `session.reasoning_objects` copy ignores caller scope (§2.8) — same class of bug as the audit's CRITICAL #1, unfixed in both `/api/chat` and `/api/compare`. |
| `mistral-7b-context-grounding-failure` | high | OPEN | A producer-reliability finding, not a comparator one — `mistral-7b` failed to ground answers in real context in 3 of 4 live runs. |
| `ollama-determinism` | medium | OPEN | The documented seed+temperature=0 determinism claim is measurably false for `qwen2.5:7b`. |
| `input-provenance-heuristic` | medium | OPEN | The UI's ✓/⚠ marker can't distinguish a legitimate derived ratio from one not grounded in input. |
| `no-route-tests` | medium | OPEN | 3 tests now exist for one route; every other route and all browser JS remain untested at the HTTP/DOM level. |
| `retry-halt-unproven` | medium | UNVERIFIED | ADR-07's retry/halt path has 24 real live runs behind it (per the ledger's own count — not yet updated to include the 4 overlapping-concept runs, which also showed zero retries/halts) and zero real-world firings — proven only against scripted mock failures. |
| `gemini-key-unconfirmed` | low | OPEN | Configured, never observed making a successful call. |
| `http-no-model-override` | low | OPEN | Per-producer model override exists in the API but has not been exercised with two genuinely different models through the UI. |
| `escalation-undefined` | info | BY_DESIGN | No workflow for what happens after a flag fires — a stated scope boundary, not an oversight. |
| `recipe-adoption` | info | BY_DESIGN | Whether to adopt the repo's existing scaffolded contradiction-detection recipe is undecided. |

**The one thing every "RESOLVED" row above does *not* claim:** this subsystem still cannot tell a
legitimate derived ratio (an ROA, a turnover ratio) from a fabricated one using number-extraction
and concept-tagging alone — both are, by definition, numbers with no recognizable concept keyword
nearby. `concept_aware` narrowed the false-positive surface from "any disjoint-concept number" to
"any untagged number"; it did not close that gap. Closing it needs either (a) verifying an
untagged number against the producer's own reported input concepts (which `validation/verification.py`
does structurally, but for citation-backed claims only — see §2.6's own limit), or (b) giving the
two producers genuinely overlapping concepts so a real same-fact disagreement becomes observable at
all — neither has been built.

**Not deployable, stated plainly:** with `audit-criticals` still open, no recipe in this repo should
claim `RUNNABLE-LIVE` on top of this layer, and this system should not be exposed to any network
beyond localhost.

---

## 8. File layout reference

```
core/            schemas, parsing, directive, contracts, numeric — the stdlib-only foundation
pipeline/        middleware (ADR-07 loop), observability (LangFuse tracing wrapper)
adapters/        gemini, ollama, mock, fixture + registry.py (provider selection, OCP)
datasources/     edgar.py — the only module that touches the network (P2)
producers/       lens.py (ConceptLens, run_lens) + financial.py, earnings.py (the two lens values)
validation/      claims, verification, consistency, cross_validation, concept_linkage
web/             FastAPI server, JWT auth, SQLite store, self_report.py, step_trace.py, React UI (web/frontend/)
tests/           242 stdlib unittest tests, incl. fixtures/ (the real-run regression corpus)
scripts/         manual, non-automated: live LLM runs, a LangFuse smoke check
docs/            this file, DATA_CONTRACT.md
divij/           proposal, SDD, diagnosis writeups, work.md (AI-authorship log)
logs/RUN_LOG.md  the canonical, append-only, dated record of every change to this subsystem
```

---

## 9. Sources this document synthesizes

`core/schemas.py`, `core/parsing.py`, `core/directive.py`, `pipeline/middleware.py`,
`validation/cross_validation.py`, `validation/concept_linkage.py`, `validation/consistency.py`,
`core/numeric.py`, `producers/lens.py`, `producers/financial.py`, `producers/earnings.py`,
`web/server.py`, `web/db.py`, `web/auth.py`, `web/self_report.py` (read directly, not assumed);
`divij/cross-agent-validation-proposal.md`, `divij/sdd.md`,
`divij/cross-agent-validation-disjoint-concepts-diagnosis.md`, and `logs/RUN_LOG.md`'s 2026-08-14
through 2026-09-11 entries (read for the historical rationale behind decisions the code itself
doesn't narrate). Per this subsystem's own rule, this document does not introduce any new claim
about behaviour without a source above it — where a fact is only observed in one live run, that is
stated as a specific run, not generalized into a rate.
