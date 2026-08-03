# Work Schedule — Accountability Mesh + Financial Analyst Agent
**Fellowship weeks 1–5 complete. Currently between Week 5 and Week 6 (Jun 6–8 weekend).**
**Week 6 starts Jun 9. Remaining contract: Week 6 → August 31 ≈ 12 weeks.**
**Pace:** ~20 hours/week | ~12h build / 4h research / 4h writing

---

## SDD v2 Decisions (carry into every week)
- **UN-03 is dormant** — single-agent system cannot satisfy inter-agent disagreement. Do not claim it satisfied until Controller Extension ships.
- **Confidence penalty is a live risk** — minus 0.1 per SIMULATED/FAILED source is running uncalibrated. Week 13 is the named mitigation date. Do not defer further.
- **ADR-10** — SQLite prototype is intentional. PostgreSQL migration is Phase 2.
- **ADR-11** — Directive v1.1.0 is the floor. Any new directive version must meet or exceed mechanical enforcement. Never revert to polite request format.
- **Problem Statement scope** — this is a single-agent prototype staged for a four-agent pipeline. Do not describe it as four agents running until the Controller Extension ships.

---

## Program Rules (carry into every week)
- **GRU session required** before any new Phase build begins — no building without a map
- **CRITIQ review required** before any Substack article is published
- **Weekly /hai report** filed to humanitarians.ai/notes
- **Claude artifacts** published to https://divij-pawar.github.io/mycroft-accountability/

---

## North Star
Take the layer from *process accountability* (well-formed, traceable, reproducible) to *ground-truth accountability* (causally verified, calibrated, outcome-tracked) — then prove it by wrapping a real financial analyst agent.

---

## COMPLETED (Weeks 1–5)

### Week 1 — May 4–10 ✅
- SDD v1.0 produced with GRU
- Substack article 1: *"Statelessness is a liability"* (live)
- Architecture defined: ReasoningObject, Checkpointing, Adversarial Arbitration

### Week 2 — May 11–17 ✅
- Phase 1 + 2 complete (94 tests)
- Enforcement layer (retry-then-halt), provider agnosticism refactor
- LangSmith evaluated and rejected (mutable ≠ audit trail)
- ADR-06 encoded
- Substack article 2: *"Can the audit layer audit itself?"* (live)

### Week 3 — May 18–24 ✅
- Inversion Audit Engine designed (Strategy A: Cycle-Consistency Inversion)
- Causal Divergence Delta (Δ) metric defined
- Three failure modes pre-specified: Judge-of-a-Judge paradox, sycophantic mirroring, delta calibration
- Week 3 article drafted (held for CRITIQ + test results)

### Week 4 — May 25–31 ✅
- Addams renewal report filed (weeks 1–3 documented, renewal gate cleared)
- Week 4 commitments: GRU SDD for Inversion Audit Engine + deploy + test
- *(Artifacts/outcomes from this week not yet fully documented — update when Week 4 /hai report available)*

### Week 5 — Jun 2–8 ✅
- *(Currently completing — update with actual deliverables when Week 5 /hai report is filed)*

---

## ACTIVE + UPCOMING WEEKS

### Week 6 — Jun 9–15 | Determinism + Ollama Adapter ✅ (complete)
**Theme:** Same input → same output. Finance auditors must re-run and get the same grade.

| Block | Hours | Deliverable | Status |
|---|---|---|---|
| Build | 12h | • `temperature → 0.0` default across Gemini adapter + new Ollama adapter<br>• `seed` parameter on both; stored in `config_snapshot` on every run<br>• `_canonicalize()` — Unicode NFC + strip + collapse whitespace — applied before prompt assembly and consistency probe<br>• `POST /api/runs/{id}/replay` — reconstructs adapter from stored config, diffs conclusions | ✅ Done |
| Research | 4h | Ollama deterministic generation guarantees; seed reproducibility caveat documented (holds within model version/quant only) | ✅ Done |
| Write | 4h | Substack draft: *"Determinism Is a Feature, Not a Default"* | ✅ Draft at `context/article_week6_determinism.md` |

**Milestone:** Ollama adapter live, seed stored per-run, replay endpoint returns `match` + `diff_chars`. Article drafted, needs CRITIQ before publication.

**Determinism caveat (documented in codebase):** seed + temperature=0 guarantees byte-identical output within a fixed model version and quantisation. Not guaranteed across model upgrades, quant changes, or Ollama version upgrades. Model name stored per-run so conditions can be reconstructed for audit.

---

### Week 7 — Jun 16–22 | Consistency Probe at Scale + Claim Verification Part 1 ✅ (build complete)
**Theme:** Verification, not just extraction. The #1 finance-specific gap.

| Block | Hours | Deliverable | Status |
|---|---|---|---|
| Build | 6h | Consistency probe auto-on for Ollama. `number_divergence_flag` hard flag — any number appearing in one run only is flagged, not just scored. | ✅ Done |
| Build | 6h | `verification.py` — citation URL fetching, SEC EDGAR `companyfacts` JSON parsing, `verified: True/False/None` per citation, `verification_rate` surfaced in UI. | ✅ Done |
| Research | 4h | SEC EDGAR `companyfacts`/`companyconcept` APIs; XBRL tag mapping | ✅ Done |
| Write | 4h | Publish Week 6 determinism article (CRITIQ first). | Needs CRITIQ |

**Milestone:** A run citing a real 10-K URL gets its numbers checked against the source automatically. Number divergence between probe runs surfaces as a hard flag in the UI.

---

### Week 8 — Jun 23–29 | Claim Verification Part 2 + Gargi Sync
**Theme:** Generalize verification; establish first teammate interface.

| Block | Hours | Deliverable |
|---|---|---|
| Build | 12h | `VerificationSource` abstract interface — works standalone now, Gargi's extractors drop in later as a higher-quality source. Generalize beyond EDGAR (PatentsView for patent claims). Verification rate badge on dashboard. |
| Research | 2h | Read all Gargi Gokhale Substack posts (SEC/XBRL, PatentsView, STOCK Act) — these define the data contracts you'll verify against |
| Collaborate | 2h | Sync with Gargi: agree on data contract (what her extractors output, what your verifier expects) |
| Write | 4h | Substack draft: *"Extraction Isn't Verification"* |

**Phase A Milestone (Weeks 5–8):** Layer is deterministic, structurally verified (Inversion Audit Engine), and claim-verified. Re-assess: sufficient for finance? Answer: yes for factual accountability; not yet for multi-agent pipeline, outcomes, or calibration.

---

### Week 9 — Jun 30–Jul 6 | Orchestration Decision + Analyst Skeleton
**Theme:** Pick the framework. Build the bones of the analyst agent.

| Block | Hours | Deliverable |
|---|---|---|
| Spike (1 day) | 8h | LangGraph vs. LangChain vs. bespoke. **Recommendation: LangGraph** — graph control flow is auditable; each node wraps through existing `run_validation_loop`; graph state = audit trail. Time-box to 1 day, decide. |
| Build | 8h | Analyst skeleton as LangGraph DAG: `data-gathering → [4 grader nodes] → aggregation`. Each node = one accountability-wrapped run. Stub financial/patent/earnings/competitive graders with clean interfaces matching Adwait's structure. |
| Write | 4h | ADR documenting orchestration choice and framework-agnostic boundary. File /hai report. |

**Milestone:** Pipeline runs end-to-end with stubs; every node logs a traceable run record.

---

### Week 10 — Jul 7–13 | Financial Grader (Full Implementation)
**Theme:** First real grader, end-to-end.

| Block | Hours | Deliverable |
|---|---|---|
| Build | 12h | Financial grader: consumes EDGAR/Gargi data → graded sub-assessment. Fully wrapped: claims extracted + verified, confidence score, consistency probe, Causal Divergence Delta if Inversion Engine is live. |
| Research | 4h | Adwait's *"Letter Grade Is the Problem"* — understand his grading rubric in full detail |
| Collaborate | 2h | Sync with Adwait on grade schema (compatibility for hybrid drop-in) |
| Write | 2h | Publish Week 8 verification article |

**Milestone:** Financial grader produces a grade + full receipt for a real ticker.

---

### Week 11 — Jul 14–20 | Remaining Graders + Aggregation
**Theme:** Complete the 4-agent pipeline.

| Block | Hours | Deliverable |
|---|---|---|
| Build | 14h | • Patent grader (PatentsView — Gargi's domain)<br>• Earnings grader (EDGAR earnings releases, call transcripts)<br>• Competitive grader (news + relative metrics)<br>• Aggregation node: combines 4 graded sub-assessments into one recommendation with per-grade audit trail |
| Research | 2h | PatentsView API (patent grant data, assignee search) |
| Write | 4h | Substack: *"Why a Letter Grade Needs a Receipt"* — flagship post, responds directly to Adwait. **This is your highest-visibility contribution.** |

**Milestone:** Ticker in → graded recommendation out. Every claim traced, every grade receipted.

---

### Week 12 — Jul 21–27 | Integration Polish + AAN
**Theme:** Wire everything together. Build the independent challenger.

| Block | Hours | Deliverable |
|---|---|---|
| Build | 12h | • Wire analyst pipeline into web UI (run detail shows full grader tree + verification status)<br>• AAN: sees primary conclusion but NOT thought_log. Independently re-derives, flags material contradictions. Use **Gemini as challenger** (Ollama = primary — genuinely different weights = real independence).<br>• AAN result stored as separate run record, linked via `parent_run_id` |
| Research | 4h | Debate/critic-model literature; adversarial-gating patterns |
| Write | 4h | ADR for AAN independence constraints. Publish "receipt" article. |

**Milestone:** Every recommendation has been challenged by an independent model before being returned.

---

### Week 13 — Jul 28–Aug 3 | Outcome Tracking + Confidence Penalty Calibration
**Theme:** "Was the grade right?" becomes a queryable metric. This week also closes the active risk on the confidence penalty model — the named mitigation date from the risk register.

| Block | Hours | Deliverable |
|---|---|---|
| Build | 10h | • Grade a ticker **as-of a historical date** (use only data available at that date — avoid look-ahead bias)<br>• Compare recommendation to realized outcome (yfinance, free)<br>• `outcomes` table linked to run records<br>• `GET /api/runs/{id}/outcome` endpoint |
| Build | 4h | **Confidence penalty calibration** — run backtested grades against the penalty model. Check whether minus 0.1 per SIMULATED/FAILED source produces sensible confidence scores vs realized outcomes. If miscalibrated, a new ADR is required and the penalty value changes. This is not optional — the risk register names Week 13 as the mitigation date. |
| Research | 2h | yfinance API; historical EDGAR filing dates (critical: avoid look-ahead bias) |
| Write | 4h | Draft: *"Was the Grade Right? Calibrating an AI analyst against the tape"* |

**Milestone:** ≥20 historical backtested grades with realized outcomes stored. Confidence penalty model either validated or a new ADR filed with a corrected value. The active risk in Section 10 item 6 of context.md closes or escalates — no third option.

---

### Week 14 — Aug 4–10 | Confidence Calibration
**Theme:** When the system says 0.8, is it right ~80% of the time?

| Block | Hours | Deliverable |
|---|---|---|
| Build | 12h | • Group backtest outcomes by confidence band (0.0–0.2, 0.2–0.4, 0.4–0.6, 0.6–0.8, 0.8–1.0), measure actual accuracy per band<br>• Reliability curve (calibration plot) — dashboard panel<br>• `GET /api/calibration` endpoint |
| Research | 2h | Platt scaling / isotonic regression for post-hoc calibration if raw calibration is poor |
| Write | 6h | Publish calibration article |

**Milestone:** Reliability curve showing whether stated confidence tracks realized accuracy.

---

### Week 15 — Aug 11–17 | SDD v2.0 + Phase 2 Deferred Items Review
**Theme:** Update the formal design document to reflect what was actually built. Assess Phase 2 deferred items.

| Block | Hours | Deliverable |
|---|---|---|
| Build | 4h | Spillover/hardening buffer |
| Write (GRU session) | 8h | SDD v2.0 — updated component registry, ADR list, constraints, external dependencies reflecting the built state |
| Research | 4h | Assess Phase 2 deferred items: OIDC migration path, 7-year retention architecture, AAN multi-agent expansion |
| Write | 4h | Publish Week 4 article if still pending. File /addams renewal if needed. |

---

### Week 16 — Aug 18–24 | Interpretability Essay + Hardening
**Theme:** The capstone research thread. Tie mechanistic interpretability to ADR-06.

| Block | Hours | Deliverable |
|---|---|---|
| Build | 6h | Final hardening. Evaluation report: what the layer catches, what it misses. |
| Research + Write | 10h | Capstone essay: *"Can a Model's Explanation Be Trusted?"* — use local Ollama models to inspect activations (logit lens / `transformer_lens`). This is the empirical complement to the Week 3 Inversion Audit Engine test. Ties mechanistic interpretability back to ADR-06 honestly. |
| Write | 4h | Final README + architecture docs for handoff/merge into Mycroft repo. |

---

### Week 17 — Aug 25–31 | Final Consolidation + Handoff
**Theme:** Land the plane.

| Block | Hours | Deliverable |
|---|---|---|
| Build | 4h | Any outstanding spillover |
| Write | 8h | Final /addams renewal or closing report. All Claude artifacts confirmed live. All /hai reports filed. |
| Collaborate | 4h | Confirm all teammate interfaces are documented and handoff-ready (Gargi VerificationSource, Adwait grade schema, AAN independence) |
| Write | 4h | Publish capstone essay (CRITIQ required first) |

---

## Total Deliverables by August 31

### Code
- Deterministic accountability layer (seed, temperature=0, replay endpoint)
- Ollama adapter (first-class, seed-based determinism)
- Inversion Audit Engine (Cycle-Consistency Inversion, Causal Divergence Delta)
- Claim verification against SEC EDGAR + VerificationSource interface
- LangGraph financial analyst (4 graders + aggregation + AAN)
- Outcome/calibration tracking with reliability curve
- SDD v2.0

### Writing (Substack + Internal)
1. *(Live)* *"Statelessness is a liability"* — Week 1
2. *(Live)* *"Can the audit layer audit itself?"* — Week 2
3. *(Pending CRITIQ)* Week 3 article — Inversion Audit Engine / Causal Divergence Delta
4. *"Determinism Is a Feature, Not a Default"* — Week 5
5. *"Extraction Isn't Verification"* — Week 7
6. *"Why a Letter Grade Needs a Receipt"* — Week 10 (flagship)
7. *"Was the Grade Right?"* — Week 12
8. *"Can a Model's Explanation Be Trusted?"* — Week 15 (capstone)

### Collaboration Interfaces
- Gargi: `VerificationSource` contract (Week 7)
- Adwait: grade schema compatibility (Week 9)
- AAN: Gemini-as-challenger independence pattern (Week 11)

---

## Risk Register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Sycophantic mirroring confirmed in Week 4 test | Medium | Week 5 Ollama adapter becomes Strategy B substrate; schedule shifts but doesn't break |
| Gemini free-tier rate limits | High | Ollama does all bulk work; Gemini only arbitrates (AAN) |
| Teammate interfaces not ready | Medium | All stubs work standalone; teammates are an upgrade, not a dependency |
| Calibration needs enough samples | Medium | Start backtesting in Week 12 early, not at the end |
| AAN threshold calibration | Medium | Build threshold as a configurable parameter with documented rationale |
| LangGraph learning curve | Low | 1-day spike in Week 8. If it doesn't fit, bespoke — the accountability core is framework-agnostic |

---

## Key Design Constraints (carry into every week)

- **Append-only DB** — never UPDATE or DELETE run records (SQLite triggers enforce this)
- **ADR-06** — thought_log is output, not process evidence. Never claim it proves reasoning was real.
- **No Claude/Anthropic attribution in commits**
- **GRU before every new Phase build**
- **CRITIQ before every Substack publication**
- **SDD scope: prototype uses SQLite; production target is PostgreSQL + JSONB**
