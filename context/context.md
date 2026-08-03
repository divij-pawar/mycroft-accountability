# Project Context — Accountability Mesh (Project 27)
**Last updated:** 2026-06-19 (Week 7 build complete)

---

## 1. What This Project Is

**Formal name:** Accountability Mesh — Stateful Audit Layer for Mycroft (Project 27)
**Program:** Humanitarians.ai OPT fellowship — "Irreducibly Human" curriculum
**Fellow:** Divij Pawar | MS Computer Science, UMass Lowell
**Repository:** `divij-pawar/mycroft-accountability` (private)
**Published artifacts site:** https://divij-pawar.github.io/mycroft-accountability/
**Substack:** https://mycroftproject.substack.com (Mycroft Series)
**Working directory:** `C:/Users/divij/Desktop/mycroft/accountability_layer/`
**Run command:** `uvicorn web.server:app --reload --port 8000` (from `accountability_layer/`)

### Core Thesis (from Week 1 article, held consistently across all three weeks)
*"An unauditable conclusion is a system failure — no matter how accurate it is."*

The Accountability Mesh transforms Mycroft from a system that produces results into a system that produces **verifiable intelligence**. It does not change what Mycroft concludes. It creates a permanent, immutable record of how it concluded it.

### Current Deployment State (as of SDD v2)
Single-agent prototype. The four-grader pipeline (financial, patent, earnings, competitive) and the AAN are designed and schema-ready but not running. The v1 SDD Problem Statement described a weighted combination of financial metrics, patent analysis, earnings execution, and competitive benchmarking — that is the design target, not the deployed system. The audit infrastructure is staged and ready to receive the multi-agent pipeline when the Controller Extension ships. Until then, one agent runs per request.

### What this system IS
- A stateful, append-only audit log of every Mycroft analysis run
- A structured reasoning enforcement mechanism via system directive injection
- A tiered access interface (investor-facing and reviewer-facing rendering)
- An observability surface for logic drift detection over time

### What this system IS NOT
- A fact verification system
- A hallucination prevention system
- A grade override mechanism
- A compliance archive (Phase 1 scope = 90-day debuggability only)

---

## 2. Program Tooling (Humanitarians.ai)

| Tool | Purpose | Status |
|---|---|---|
| **GRU** | SDD consultant — required before any new Phase build begins | Used in Week 1 (SDD v1.0). Required again before Week 4 Inversion Audit Engine test. |
| **CRITIQ** | Article review before Substack publication | Deferred — required before Week 3 article publishes |
| **ADDAMS** | Hull House / OPT Documentation System — produces /hai and /addams reports | Used for all weekly reports |

**Rule:** No new Phase build without a GRU SDD. No Substack publication without CRITIQ review.

---

## 3. The Mycroft Portfolio — Who Builds What

Source: https://mycroftproject.substack.com

The portfolio is organized around a multi-agent financial analyst. Key colleagues:

### Adwait Changan
- **Post:** *"Letter Grade Is the Problem"* — argues a single letter grade (A/B/C) destroys the reasoning that produced it. Advocates for structured sub-assessments.
- **Relevance:** His grading rubric is upstream of your aggregation node. Schema compatibility needed.

### Gargi Gokhale
- **Posts:** SEC/XBRL structured data extraction, PatentsView API integration, STOCK Act disclosure data.
- **Relevance:** Her extractors are a higher-quality `VerificationSource` than raw URL fetching.

### Tanmay Kulkarni, Ameya Deshmukh, Rithanya Chandran
- Additional pipeline contributors; Ameya is a downstream consumer of accountability outputs.

---

## 4. Fellowship Weekly Record (Weeks 1–3 Complete)

### Week 1 — May 4–10 | Problem Definition
- **No inherited codebase** — project invented from first principles
- **Deliverables:** Substack article (*"Statelessness is a liability"*) + SDD v1.0 (produced with GRU)
- **Three mechanisms defined:** ReasoningObject (signed agent affidavit), Checkpointing (forensic state snapshots), Adversarial Arbitration (hard-coded conflict surfacing)
- **Key framing:** "Accuracy is not the same as trustworthiness. A system that is right 90% of the time and cannot tell you why it was right offers you no mechanism for identifying the 10%."

### Week 2 — May 11–17 | Build (Phase 1 + 2 Complete)
- **Phase 1 complete:** Data contracts, schemas, confidence scoring, tiered access separation
- **Phase 2 complete:** Enforcement layer with retry-then-halt logic (94 tests passing)
- **Provider agnosticism:** Layer refactored to wrap any agent regardless of model/platform
- **LangSmith decision:** Evaluated and rejected as system of record
  - *"LangSmith traces can be deleted. That is not a feature gap — it is a category difference. A mutable audit trail is not an audit trail for compliance purposes. It is a debugging log."*
  - LangSmith retained as development debugging tool only
- **ADR-06 encoded:** thought_log is evidence of output, not evidence of process
- **First integration target identified:** LangChain financial analyst agent on Gemini

### Week 3 — May 18–24 | Audit the Audit (Inversion Audit Engine Design)
- **Problem confronted:** The enforcement layer validates structure, not truth. A post-hoc rationalization passes structural validation.
- **Approaches evaluated and rejected:**
  - LLM-as-a-Judge: a critic model validates narrative plausibility — a highly capable model can validate a well-written post-hoc rationalization as easily as genuine reasoning. Category error.
  - White-box gradient inversion: infrastructure overhead forces abandonment of frontier closed models.
- **Strategy A (selected): Cycle-Consistency Inversion (Inversion Audit Engine)**
  - Treats the agent's conclusion as a "compiled asset"
  - Attempts to reverse-engineer the inputs
  - Calculates a **Causal Divergence Delta (Δ)** — causal information density required to justify the conclusion, not resemblance to input data
  - Inputs: `thought_log`, `conclusion`, ground-truth input data `X`
  - Outputs: reconstructed input matrix `X'`, delta score `Δ`
  - KPI: Causal Divergence Delta feeds web-UI dashboard
- **Strategy B (fallback):** Local open-weights with gradient interrogation — required if sycophantic mirroring is confirmed

### Unresolved Problems Carried Into Week 4
1. **The Judge-of-a-Judge Paradox:** The Inversion Audit Engine uses one frontier model to generate the thought_log and a second to invert it. High delta = either (a) original log was post-hoc rationalization OR (b) decompiler hallucinated. These two failure modes produce identical output. No way to distinguish a flawed audit trail from a flawed auditor.
2. **The Sycophantic Mirroring Failure Mode (pre-specified):** False negative where Δ=0 but the log is a fabrication. Mechanism: decompiler uses its pre-trained world-knowledge to reconstruct a fake input matrix that matches the true input by statistical correlation rather than causal extraction. If confirmed → Strategy B required.
3. **Delta Calculation Matrix:** Current threshold for "exploit flag" is a calibrated guess. Financial inputs mix quantitative row data with qualitative directional sentiment — a scalar delta weighting a missing decimal point against a flipped directional signal does not yet exist.

### Week 7 — Jun 16–22 | Consistency Probe at Scale + Claim Verification Part 1
- **Consistency hard flag:** `number_divergence_flag: bool` added to `ConsistencyResult`. Set `True` whenever any number appears in one conclusion but not the other (the "appears-in-one-only" condition). Surfaced prominently in the detail modal as a red badge, not buried in the score.
- **Consistency probe auto-on for Ollama:** `probe_enabled = _config["consistency_probe"] or _config["provider"] == "ollama"`. Bulk Ollama runs are rate-free so the doubling cost is acceptable; double-checking becomes the default.
- **`verification.py` new module:** For each `[SOURCE: label, url]` citation claim, fetches the source URL and checks whether quantitative values from the thought_log appear in it. SEC EDGAR `companyfacts` JSON parsed for XBRL numeric facts; generic URLs searched for matching number strings. Returns `verified: True/False/None` per citation (None = unattainable). Deduplicates URL fetches. 1% relative tolerance for numeric matching.
- **`verified` type change on `ExtractedClaim`:** `bool = False` changed to `bool | None = None` (True=confirmed, False=checked/not found, None=unattainable/unchecked).
- **`verification_rate` added to run payload:** fraction of citation claims confirmed against their sources. Surfaced as "X% verified" badge on the claims summary line in chat and detail views.
- **Article written:** *"Determinism Is a Feature, Not a Default"* — Week 6 article draft complete at `context/article_week6_determinism.md`. Ready for CRITIQ before Substack publication.

### Week 4 Commitments (Current Week — Jun 2–8)
1. Run GRU session — produce SDD for Inversion Audit Engine **before** any test code is written
2. Deploy Inversion Audit Engine to test harness, run cycle-consistency inversion against known-hallucinated output
3. Probe sycophantic mirroring failure mode (pre-specified test case)
4. Document results honestly — if sycophantic mirroring confirmed, pivot to Strategy B
5. CRITIQ review on Week 3 article, publish if test results support thesis
6. Publish Week 4 Claude artifact to https://divij-pawar.github.io/mycroft-accountability/

---

## 5. Architecture — Full SDD Component Registry

Source: SDD_AccountabilityMesh_v1.pdf (v1.0, 2026-04-26)

### Execution Flow
```
Client Request
  ↓
C-01 Audit Middleware ← Opens RunSession, injects versioned system directive
  ↓
C-07 Sub-Agents ×4 [Financial | Patent | Earnings | Competitive]
  Each returns: <thought_log> + <conclusion> + citations
  ↓
AAN Adversarial Arbitration Node ← Triggered on semantic divergence only
  Single debate loop → Affidavit of Disagreement (resolved or unresolved)
  ↓
Controller [standard | post-debate | unresolved-divergence inputs]
  ↓
C-02 ReasoningObject Writer ← Pydantic validation + validation loop on parse failure
  HALT if write fails — no grade without a complete audit trail (F-01)
  ↓
C-03 RunRecord Store — PostgreSQL + JSONB ← Append-only. No UPDATE. No DELETE.
  ↓
C-08 Confidence Classifier / Delivery Gate
  score ≥ 0.4 → STANDARD   score < 0.4 → HIGH UNCERTAINTY / SPECULATIVE
  ↓                          ↓
C-04 Query API (investor)   C-04 Query API (auditor)
  ↓                          ↓
C-05 Investor Trace Renderer  C-06 Full Log Renderer
```

### Component Descriptions

**C-01 Audit Middleware** [New] — Active participant in agent execution, not a passive observer. Opens RunSession, injects versioned directive into every agent prompt, validates responses via Pydantic, executes validation loop on parse failure, writes ReasoningObjects, halts on write failure. Needs: UN-01, UN-04, BN-01

**C-02 ReasoningObject** [New] — Atomic unit of the audit log. One per agent per run, plus one per failed validation attempt. Never mutated after write. Key fields: `agent_id`, `run_id`, `attempt_number`, `parse_status` (SUCCESS/PARSE_FAILURE/HALT), `data_sources`, `confidence_score`, `confidence_degradation_reason`, `thought_log`, `reasoning_steps`, `conclusion`, `citations`, `raw_output`, `llm_tokens`, `data_quality_warnings`. Needs: UN-01, UN-03, UN-04, BN-01, BN-03

**C-03 RunRecord Store** [New] — PostgreSQL with JSONB (current prototype: SQLite). Append-only. One row per run per ticker. Reviewer flags in separate `reviewer_flags` table. DB-layer trigger blocks UPDATE and DELETE unconditionally. Application user: INSERT and SELECT only. TTL: 90 days. Index: ticker + initiated_at. Needs: UN-04, BN-01, BN-02

**C-04 Query API** [New] — Read-only. Two route families by JWT scope. Investor routes: field-level DB projection (thought_log, raw_output, llm_tokens never selected). Separate route handlers per scope. Needs: UN-04, UN-05, BN-02

**C-05 Investor Trace Renderer** [New] — Plain-English structured narrative. "HIGH UNCERTAINTY / SPECULATIVE" header must be visually unambiguous (ADR-08). Per-agent summary. Data quality callouts inline. AAN notice if triggered. Needs: UN-01, UN-02, UN-03

**C-06 Full Log Renderer** [New] — Full evidentiary record. Full ReasoningObject per agent/attempt. thought_log raw. Data source table. Validation loop record. AAN debate transcripts. Reviewer flag interface. Structured for failure-class diagnosis. Needs: UN-04, UN-05

**C-07 Existing Mycroft Agents ×4** [Existing — behaviorally modified] — Financial, Patent, Earnings, Competitive. Not modified at code level. Behaviorally modified by system directive from C-01. Required output format: `<thought_log>` block → `<conclusion>` block → citations. Directive is versioned, hardcoded in source — not configurable at runtime. Needs: UN-01, BN-03

**C-08 Confidence Classifier / Delivery Gate** [New] — Score ≥ 0.4: STANDARD. Score < 0.4: HIGH UNCERTAINTY / SPECULATIVE header. All runs produce investor trace — suppression on low confidence creates silent incompetence (ADR-08). Needs: UN-02, ADR-08

**AAN Adversarial Arbitration Node** [New — Project 27 component] — State-triggered sub-orchestrator. Invoked when semantic divergence exceeds threshold (delta between confidence scores and sentiment vectors). Single debate loop. Max iterations: 1. Unresolved divergence → Affidavit of Disagreement → Controller (not a halt). Audit layer records AAN execution; AAN is owned by Project 27 pipeline. **Constraint 5: divergence threshold not yet defined.** Needs: UN-03, BN-01, T-04 partial mitigation

### Component Status and Traceability Matrix
Status values: **BUILT** / **SCHEMA-READY** (upstream-blocked) / **NOT BUILT**

| Component | Status | Needs Traced | Currently Satisfied? |
|---|---|---|---|
| C-01 Audit Middleware | BUILT | UN-01, UN-04, BN-01 | Yes |
| C-02 ReasoningObject | BUILT | UN-01, UN-03, UN-04, BN-01, BN-03 | Partial — UN-03 dormant (see below) |
| C-03 RunRecord Store | BUILT (SQLite) | UN-04, BN-01, BN-02 | Yes |
| C-04 Query API | BUILT | UN-04, UN-05, BN-02 | Yes |
| C-05 Investor Trace Renderer | BUILT | UN-01, UN-02, UN-03 | Partial — UN-03 dormant |
| C-06 Full Log Renderer | BUILT | UN-04, UN-05 | Yes |
| C-07 Existing Mycroft Agents ×4 | SCHEMA-READY (upstream-blocked) | UN-01, BN-03 | No — Controller Extension not delivered |
| C-08 Confidence Classifier | BUILT | UN-02, ADR-08 | Yes |
| AAN | SCHEMA-READY (upstream-blocked) | UN-03, BN-01, T-04 partial | No — Controller Extension not delivered |

**UN-03 — DORMANT.** "Investor: know when agents disagreed" is structurally unmeetable in a single-agent system. There is no second agent to disagree with. The schema fields (`aan_triggered`, `aan_affidavit`) exist and are ready. The need is not removed. It is dormant until the Controller Extension ships and the AAN is exercised. The v1 traceability matrix claimed this satisfied — that claim was incorrect and is corrected here.

---

## 6. Architecture Decision Records (Full List)

### ADR-01b — Active middleware, not a passive observer *(supersedes ADR-01)*
Audit middleware injects a system directive into every agent prompt. Pure boundary observation cannot capture intra-agent reasoning. Consequence: directive changes affect all four agents simultaneously and require a code deployment.

### ADR-02 — Flags as a separate table, never written to RunRecord
Reviewer flags in `reviewer_flags` with FK to `run_id`. RunRecord row never updated. Preserves immutability. Consequence: queries combining run + flags require a join.

### ADR-03 — Single event stream, two rendering tiers
One RunRecord per run. Investor summary and full log are rendering concerns, not storage concerns. The rendering logic in C-05 and C-06 must be explicitly tested for information leakage.

### ADR-04 — confidence_score is computed, not self-reported
Reduced programmatically when `data_quality_warnings` are present. Agents cannot be trusted to assess their own input quality. Penalty: -0.1 per SIMULATED or FAILED source.

### ADR-05 — System directive stored verbatim per run
Exact directive text stored in RunSession at run time (not a config file pointer). Directive version is part of run identity for drift detection.

### ADR-06 — thought_log is evidence, not ground truth *(The Trust Problem)*
The thought_log is an LLM-generated artifact, not a verified record of machine reasoning. A hallucinated conclusion may be accompanied by a coherent, plausible-sounding thought_log. **"The log is evidence of output, not evidence of process."** Reviewers must be trained on this distinction. This is a property of LLM-generated reasoning that no logging layer can fix — encoded honestly rather than papered over.

### ADR-07 — Validation loop with corrective re-invocation
Single retry on structural parse failure with explicit corrective directive: *"Your previous response failed structural validation. You must provide your internal reasoning in a <thought_log> block..."* Both attempts written to RunRecord. Halt on second failure. Both attempts always written — rising parse_failure_rate is a first-class drift signal.

### ADR-08 — Low confidence delivery is transparent, not suppressed
Runs with score < 0.4 delivered with HIGH UNCERTAINTY / SPECULATIVE header, not withheld. Suppression creates silent incompetence. The trace is the evidence that the investor was warned. Header must be visually unambiguous — cannot be a footnote.

### ADR-09 — Phase 1 auth is explicitly temporary
Manual API key management (current: JWT HS256). Known security limitation. Phase 2 requires migration to standardized OIDC provider (Auth0, Cognito, or equivalent) before any production deployment involving real investor data.

### ADR-10 — SQLite for Phase 1 prototype
Decision: SQLite as dev prototype persistence layer despite PostgreSQL being specified in the SDD. Rationale: stdlib-only constraint, behavioral parity with the PostgreSQL spec verified across all requirements (append-only triggers, ticker index, TTL purge, reviewer flags table, WAL mode). Migration path is near-drop-in — schema written for compatibility. PostgreSQL migration deferred to Phase 2 alongside OIDC.

### ADR-11 — Directive v1.1.0: mechanical format enforcement
Decision: hardened the system directive to require the first character of every model response to be the opening angle bracket, with an explicit prohibition on any preamble before the `<thought_log>` tag. Rationale: the v1.0.0 assumption that models would self-comply with the two-block format was falsified against live Gemini output. The model produced numbered reasoning steps as plain text before the `<thought_log>` tag, causing false parser matches in the parser. The v1.0.0 directive was a polite request. v1.1.0 is a mechanical constraint. This falsification is the strongest available evidence for ADR-01b's "active middleware" claim — passive format requests are insufficient, directive enforcement is the correct layer.

---

## 7. Security Posture

| ID | Threat | Mitigation | Residual |
|---|---|---|---|
| T-01 | Investor accesses thought_log via parameter manipulation | SEC-01: DB-layer projection, structural schema exclusion, 403 on thought_log access attempt | Low |
| T-02 | Forged or scope-escalated JWT token | SEC-02: server-side signature verification, allowlist scope validation | Low |
| T-03 | RunRecord mutation via direct database access | SEC-03: DB-layer trigger, role-level permissions | Low |
| T-04 | Prompt injection via semantically poisoned data source | AAN cross-examination increases attack cost. Pydantic validates structure only, not semantics. If both agents ingest the same poisoned source, AAN will not detect contamination. | **Residual — documented, accepted** |

**SEC-01** — thought_log must provably never reach investor tier. Field-level DB projection. Structural schema exclusion (not null suppression). Penetration test: investor token attempting thought_log retrieval must return 403, not null.

**SEC-02** — JWT scope in allowlist-validated claims. No token caching that bypasses validation. Token expiry enforced. Scope escalation test: investor token with injected auditor scope must return 403.

**SEC-03** — PostgreSQL row-level trigger blocks UPDATE and DELETE on run_records unconditionally. Application user: INSERT + SELECT only.

**SEC-04** — System directive in versioned source code only. No runtime parameter can modify directive content. Changes require code deployment.

---

## 8. Operational Posture (OPS)

**OPS-01 — Availability (Critical):** Audit layer is in Mycroft's critical path. Write latency alert: p99 > 500ms. HALT events logged to dedicated ops channel. Pool exhaustion is a halt vector.

**OPS-02 — Validation Loop Observability (High):** `parse_failure_rate` per `agent_id`, rolling 24h window. `retry_success_rate`. `halt_rate`. Alert: parse_failure_rate > 5% on any single agent → internal reviewer notification.

**OPS-03 — TTL Enforcement (Medium):** Scheduled job purges `initiated_at < NOW() - 90 days` daily off-peak. `reviewer_flags` cascade-deletes via FK. `api_keys` excluded from TTL.

**OPS-04 — Confidence Score Monitoring (Medium):** Mean `run_confidence_score` per ticker, rolling 7-day window. Alert: score < 0.4 on delivery → internal reviewer notification.

---

## 9. User and Business Needs

### User Needs
- **UN-01** — Investor: interrogate the grade (understand data, confidence, per-agent conclusions)
- **UN-02** — Investor: know when the system is uncertain (degraded/simulated data warnings)
- **UN-03** — Investor: know when agents disagreed (AAN-triggered flag)
- **UN-04** — Internal reviewer: distinguish failure classes (data scraper failure / reasoning hallucination / structural parse failure / unresolved agent divergence)
- **UN-05** — Internal reviewer: flag a run as incorrect without altering the immutable trace

### Business Needs
- **BN-01** — Immutable provenance record per analysis (tamper-proof, reconstructable)
- **BN-02** — Logic drift detection across model updates (same ticker, different dates)
- **BN-03** — Data provenance per agent invocation (source URL, fetch status, live vs. simulated)

---

## 10. Constraints and Residual Risks

1. **Structural integrity, not fact verification** — the system audits the machine, not the world
2. **T-04 residual: semantic poisoning** — accepted, must appear in reviewer training materials
3. **90-day debuggability scope only** — compliance-grade retention (7 years) is Phase 2
4. **Phase 1 auth not production-grade** — OIDC migration required before production
5. **AAN divergence threshold not yet defined** — must be calibrated before deployment
6. **Confidence penalty model — ACTIVE RISK, uncalibrated** — the minus 0.1 per SIMULATED or FAILED source penalty is live in every run and has not been validated against real data. This is not a deferred feature. It is an untested assumption already affecting investor-facing confidence scores on every run where data source quality is degraded. The number was argued from first principles, not derived from measurement. A miscalibrated penalty model silently distorts every confidence score against degraded sources with no visible signal. This must not be treated as a Phase 2 deferral — it is a live risk accepted pending the Week 13 backtesting work, which is the named mitigation date. If backtesting reveals the model is miscalibrated, a new ADR will be required.

---

## 11. External Dependencies (from SDD §10)

**Dependency 1 — Project 27 Controller Extension (BLOCKING — not delivered)**
Controller must handle three input states: (1) standard result — no AAN, (2) post-debate resolved — AAN invoked, consensus reached, (3) unresolved divergence — AAN invoked, Affidavit passed through. A Controller that averages unresolved divergence into a neutral grade defeats the AAN entirely. As of SDD v2: the three input states are structurally present in RunSession schema. The Controller Extension has not been delivered by Project 27. The accountability layer is running as a single-agent system as a result. This is not a scope failure on the accountability layer side — the infrastructure is ready. The upstream dependency has not shipped.

**Dependency 2 — Agent Output Format Verification (partially resolved)**
System directive injection changes output format of all four agents. Verified against live Gemini output — directive v1.0.0 was falsified (preamble before thought_log, false parser matches). Fixed in directive v1.1.0 (ADR-11). Sole consumer verification cannot be completed until the Controller Extension ships and a multi-agent orchestrator exists. In the current single-agent prototype, server.py is the only consumer of raw agent output.

---

## 12. What's Built (Current State)

### Core Accountability Layer
- **`middleware.py`** — `run_validation_loop()`, `HaltError`, retry-then-halt (ADR-07)
- **`schemas.py`** — `RunSession`, `ReasoningObject`, `DataSource`, `AgentID`, `ConfidenceClassification`, `RunStatus`
- **`directive.py`** — `get_active_directive()`, versioned directive management (ADR-05)
- **`parser.py`** — structured response parsing from LLM output

### ADR-06 Mitigations (Phase 2)
- **`claims.py`** — `extract_claims(thought_log) → list[ExtractedClaim]`
  - Types: `citation` (`[SOURCE: label, url]`), `quantitative` ($, %, x, bps), `hedge`, `causal`
  - Each claim: `text`, `context`, `source_label`, `source_url`, `verified: bool | None`
    - `True` = source confirmed, `False` = source checked / not found, `None` = unattainable / not checked
- **`verification.py`** — `verify_claims(claims) → (updated_claims, verification_rate)` (Week 7)
  - For each citation with a URL, fetches source and checks whether quantitative values appear
  - SEC EDGAR `companyfacts` JSON: parses XBRL fact values, checks overlap within 1% tolerance
  - Generic URLs: regex number extraction then overlap check
  - Deduplicates URL fetches; `verification_rate` = verified_citations / total_citations
  - Always runs after `extract_claims()` on every successful run
- **`consistency.py`** — `run_consistency_probe(...) → ConsistencyResult`
  - Score = `(word_overlap × 0.4) + (number_overlap × 0.6)`
  - HIGH ≥ 0.70 / MEDIUM ≥ 0.40 / LOW < 0.40 / UNKNOWN on failure
  - `number_divergence_flag: bool` — hard flag when any number appears in one run only (Week 7)
  - Probe NOT persisted to DB
  - Auto-on when `provider == "ollama"` (Week 7); opt-in via config for Gemini

### Adapters
- **`adapters/gemini_adapter.py`** — `make_gemini_adapter(model, temperature=0.0, seed=None)`
  - Passes `seed` to `GenerateContentConfig.seed` when set
  - Default temperature changed to 0.0 (deterministic)
  - Rate limit handling: `RateLimitDailyError`, `RateLimitMinuteError`
- **`adapters/mock_adapter.py`** — `make_mock_adapter(failure_mode)` (none/retry_success/halt)
- **`adapters/ollama_adapter.py`** — `make_ollama_adapter(model="llama3.2", temperature=0.0, seed=42)`
  - stdlib only — `urllib.request`, no new dependencies
  - Posts to `OLLAMA_HOST/api/generate` (default: `http://localhost:11434`)
  - Directive injected as `system` field (ADR-01b)
  - `seed` + `temperature=0` in options → deterministic within a model version
  - `OllamaConnectionError` — Ollama not running or wrong host
  - `OllamaModelError` — model not pulled locally
  - Determinism caveat: seed reproducibility holds within a fixed model version and quantisation level; NOT guaranteed across model upgrades

### Determinism (Week 6)
- **`_canonicalize(text)`** in `web/server.py` — applied to both `message` and `context` before prompt assembly
  - Unicode NFC normalisation
  - Strip leading/trailing whitespace
  - Collapse horizontal whitespace (spaces/tabs → single space; newlines preserved)
  - Applied before `run_validation_loop` and the consistency probe
  - Canonical subject stored in run payload — replay reads back what was actually sent
- **`seed`** added to `_config` (default: 42) and `ConfigUpdate`; stored in `config_snapshot` on every run
- **Default `temperature` changed to 0.0** across config, Gemini adapter, and Ollama adapter
- **`POST /api/runs/{id}/replay`** — reconstructs adapter from `config_snapshot`, re-runs with stored directive version, returns `match`, `diff_chars`, both conclusions, original config. Not persisted.

### Web Layer (FastAPI)
- **`web/server.py`** — all routes, JWT scope enforcement, live config
  - Provider options: `mock`, `gemini`, `ollama`
  - Config keys: `provider`, `model`, `ollama_model`, `temperature`, `seed`, `agent_id`, `failure_mode`, `confidence_score`, `consistency_probe`
  - Ollama errors surfaced with 🦙 prefix in payload
- **`web/db.py`** — SQLite (prototype; SDD specifies PostgreSQL+JSONB for production)
  - Append-only via triggers. TTL purge drops/recreates no-delete trigger.
  - Ticker extraction: `re.sub(r"[^a-zA-Z0-9]", "", subject.split()[0]).upper()[:10]`
- **`web/auth.py`** — `issue_token(scope)`, `require_scope()` (FastAPI dependency), HS256 JWT, 8h TTL
- **`web/static/`** — index.html, app.js, style.css (JWT cache, consistency badges, claims pills, flag form)
  - Ollama provider option in dropdown
  - Hidden `ollamaFields` panel: model selector + seed slider (0–999)

### Test Coverage
- **94 automated tests passing** (unittest, no pytest)

---

## 13. What's NOT Built Yet

### Phase 3 — Inversion Audit Engine (Designed, Not Built)
- Strategy A: Cycle-Consistency Inversion — conclusion → reverse-engineer inputs → Causal Divergence Delta (Δ)
- Inputs: `thought_log`, `conclusion`, ground-truth input `X`; Outputs: reconstructed `X'`, delta score `Δ`
- Test scheduled for Week 4
- Requires GRU SDD before test code is written

### Phase 3 — LangChain/LangGraph Integration
- First integration target: LangChain financial analyst agent on Gemini
- 4-grader pipeline (financial, patent, earnings, competitive) + AAN
- Not yet started

### Claim Verification (Week 7 complete — citation-level)
- Citation claims now verified against source URLs
- EDGAR JSON and generic URL support done
- Next: quantitative claim-level verification (linking specific number to specific source entry)

### Outcome Tracking / Backtesting
- No `outcomes` table, no backtest, no calibration

### AAN Threshold Calibration
- Divergence threshold not yet defined (Constraint 5 from SDD)

---

## 14. Phase 2 Deferred Items

- OIDC authentication provider (Auth0/Cognito) — required before production
- Compliance-grade retention (7-year archive + cold storage tier)
- AAN multi-agent expansion (current: bilateral only)
- Investor-facing drift visualization (longitudinal ticker analysis)

**Removed from this list:** Confidence score backtesting. Moved to active risk register (Section 10, item 6). The penalty is live. Deferring its validation is a risk acceptance, not a feature deferral.

---

## 15. API Endpoints

```
POST /api/auth/token           Issue JWT (scope: auditor|investor)
GET  /                         Test interface
GET  /api/config               Current live config
POST /api/config               Update live config
POST /api/chat                 Run accountability loop (Bearer required)
GET  /api/runs                 List runs (filter: ticker, from, to, limit)
GET  /api/runs/drift?ticker=X  Confidence scores over time
GET  /api/runs/{id}            Get single run
POST /api/runs/{id}/replay     Re-run with stored config; returns match + diff (not persisted)
POST /api/runs/{id}/flags      Add reviewer flag (auditor scope only)
GET  /api/runs/{id}/flags      List flags for run
GET  /api/sessions             List sessions
GET  /api/sessions/{id}        Get session
DELETE /api/runs               Clear all (dev/test only)
GET  /api/directive            Active directive version + text
```

---

## 16. Key Technical Constraints

- **stdlib only** — no pytest (use `unittest`); document all new deps in `requirements.txt`
- **Run from `accountability_layer/`** — all imports assume this working directory
- **Lazy anthropic import** — if Claude API added, import inside function only
- **Append-only DB** — SQLite triggers block UPDATE/DELETE on `runs` table; bypass only for TTL purge
- **No Claude/Anthropic attribution in commits**
- **Production DB: PostgreSQL + JSONB** (current prototype uses SQLite per stdlib constraint)

---

## 17. Published Writing

| Article | URL | Week | Status |
|---|---|---|---|
| *"Statelessness is a liability: why your AI financial agent's result is not enough"* | https://mycroftproject.substack.com/p/statelessness-is-a-liability-why | Week 1 | Live |
| *"Can the audit layer audit itself?"* | https://mycroftproject.substack.com/p/the-audit-trail-cannot-prove-the | Week 2 | Live |
| Week 3 article (Inversion Audit Engine / Causal Divergence Delta) | TBD | Week 3 | Drafted — held pending test results, CRITIQ required |

**Key quotes:**
- *"The audit trail is not the output. The audit trail is the evidence that the output was produced by something more than pattern-matching on training data."*
- *"LLMs are world-class rationalizers. They will produce a plausible-sounding explanation for a mistake they already made — even if that explanation has nothing to do with the original reasoning."*
- *"The immutability question made the call clear. LangSmith traces can be deleted. That is not a feature gap — it is a category difference."*

---

## 18. Environment Setup

```bash
# From accountability_layer/
pip install -r web/requirements.txt

# .env in accountability_layer/ or mycroft/
ACCOUNTABILITY_SECRET=<random 32+ char string>
GEMINI_API_KEY=<your Gemini API key>
OLLAMA_HOST=http://localhost:11434   # optional — this is the default

# For Ollama provider: pull the model first
ollama pull llama3.2

# Run
uvicorn web.server:app --reload --port 8000
```

### requirements.txt (current)
```
fastapi
uvicorn[standard]
pydantic
python-dotenv
PyJWT>=2.8.0
google-generativeai  # lazy-imported
```

### .gitignore additions
```
web/data/*.db
web/data/*.json
web/data/*.json.migrated
=*
```

---

## 19. Git History (No Claude Attribution)

| Commit | Message |
|---|---|
| `1c6e306` | `feat: adr-06 mitigations — claim extraction and consistency probing` |
| `952312e` | `feat: phase 1 complete — sqlite persistence, jwt auth, ticker api, reviewer flags` |

Cleanup pending: `git push origin --delete adr-06-mitigation && git branch -d adr-06-mitigation`

---

## 20. Job Description Framing (CPT/OPT)

*"Developing and maintaining an accountability and audit layer for large language model (LLM) agents in a multi-agent financial analysis system. Responsibilities include designing append-only audit persistence using SQLite with trigger-enforced immutability, implementing JWT-based role-scoped access control, building structured claim extraction from LLM reasoning traces, developing consistency-probing mechanisms for output reliability measurement, and integrating with structured financial data sources (SEC EDGAR, XBRL) for automated fact-verification. Work directly applies graduate coursework in machine learning systems, natural language processing, and software engineering."*
