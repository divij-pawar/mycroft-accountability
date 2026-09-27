"""
Accountability Layer — Web Test Interface
Exposes the full accountability layer: RunSession lifecycle, SEC-01 scope tiers,
DataSource provenance, ADR-04 confidence degradation, ADR-08 classification.

Phase 1 additions:
  C-03  SQLite persistent store (append-only, 90-day TTL)
  C-04  Ticker query API  GET /api/runs?ticker&from&to
        Drift surface     GET /api/runs/drift?ticker
  UN-05 Reviewer flags    POST/GET /api/runs/{id}/flags
  SEC-02 JWT auth         POST /api/auth/token  →  Bearer token required on /api/chat

Run from the accountability_layer/ directory:
    uvicorn web.server:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from dotenv import load_dotenv, find_dotenv

# Search upward from cwd for a .env file (finds it in accountability_layer/ or mycroft/)
load_dotenv(find_dotenv(usecwd=True) or find_dotenv())

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

import unicodedata

# Adapter *construction* comes from the registry. LangChain is the one agent
# framework now (see adapters/langchain_adapter.py) — LangchainConnectionError
# is the one adapter-failure type both routes below catch; the separate
# Gemini/Ollama exception types this used to import are gone with those
# archived standalone adapters (archive/adapters/{gemini,ollama}_adapter.py).
from adapters.langchain_adapter import LangchainConnectionError
from adapters.registry import (
    build_model_call,
    build_adapter,
    model_label,
    resolve as resolve_provider,
    with_model_override,
)
from validation.claims import extract_claims_from_response
from validation.consistency import run_consistency_probe
from validation.cross_validation import (
    ComparisonStatus,
    list_contradictions,
    persist_cross_agent_run,
    run_cross_agent_validation,
)
from datasources.edgar import EdgarFetchError, fetch_company_facts, lookup_cik
from datasources.filings import find_excerpt
from producers.earnings import EARNINGS_LENS, summarize_earnings_facts
from producers.financial import FINANCIAL_LENS, summarize_facts
from producers.lens import shared_concepts as lens_shared_concepts
from producers import PAIRINGS
from validation.verification import verify_claims
from core.directive import get_active_directive
from pipeline.middleware import HaltError, run_validation_loop
from core.schemas import (
    AgentID,
    ConfidenceClassification,
    DataSource,
    DataSourceStatus,
    ParseStatus,
    RunSession,
    RunStatus,
)
from web.db import (
    init_db,
    purge_old_runs,
    insert_run,
    insert_session,
    get_runs,
    get_run,
    get_sessions,
    get_session,
    get_drift,
    get_flags,
    insert_flag,
    get_decisions,
    get_decisions_for,
    insert_decision,
    clear_all,
    migrate_from_json,
)
from web.auth import issue_token, optional_scope, require_scope, ScopeT
from validation.gate import (
    AWAITING_DECISION,
    strip_search_content,
    GATE_POLICY,
    DecisionError,
    gate_state,
    redact_for_scope,
    validate_decision,
)
from web.self_report import build_self_report
from web.step_trace import (
    PHASE_AGENT_A,
    PHASE_AGENT_B,
    PHASE_CHAT,
    PHASE_COMPARE,
    PHASE_SHARED,
    StepTrace,
    wrap_adapter,
    wrap_model_call,
)
from core.assessment import EXTRACTION_PROMPT_VERSION
from validation.audit import audit_record, to_markdown

STATIC_DIR  = Path(__file__).parent / "static"
LEGACY_FILE = Path(__file__).parent / "data" / "runs.json"

app = FastAPI(title="Accountability Layer — Test Interface")
# The classic UI that lived in STATIC_DIR (web/static/) was archived on 2026-09-27
# (U9 cutover) to archive/web-static-legacy/, whose README says how to restore it;
# its /static mount went with it. STATIC_DIR is kept only so that restore is a
# two-line change.

# The React UI (web/frontend/), served at /app once built (`npm run build` there),
# and at "/" since the cutover.
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"


class _FrontendFiles(StaticFiles):
    """
    index.html must be revalidated on every load: it names the current build's
    content-hashed assets, and a heuristically cached copy keeps pointing a browser
    at the previous build after a rebuild (found while verifying U1). The hashed
    assets themselves are safe to cache.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


if FRONTEND_DIST.is_dir():
    app.mount("/app", _FrontendFiles(directory=str(FRONTEND_DIST), html=True), name="app")


# ── Startup ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    init_db()
    purge_old_runs()
    # One-shot migration from legacy JSON store
    if LEGACY_FILE.exists():
        try:
            data     = json.loads(LEGACY_FILE.read_text(encoding="utf-8"))
            imported = migrate_from_json(
                data.get("runs", []),
                data.get("sessions", {}),
            )
            if imported:
                # Rename rather than delete so the file isn't lost
                LEGACY_FILE.rename(LEGACY_FILE.with_suffix(".json.migrated"))
        except Exception:
            pass  # corrupt legacy file — ignore


# ── Live config ────────────────────────────────────────────────────────────────

# "Which LLM" (model name/family, API keys) is LangChain's own concern now,
# configured by whoever runs this via environment variables — not a web UI
# control. LANGCHAIN_MODEL sets the default model name; langchain_adapter.py
# infers Ollama- vs. Gemini-family from the name itself. "provider" stays in
# this dict only because build_adapter()/model_label() read it structurally
# (adapters/registry.py) — it is fixed to "langchain" and no longer a
# ConfigUpdate field at all.
_config: dict = {
    "provider":          "langchain",
    "model":             os.environ.get("LANGCHAIN_MODEL", "llama3.2"),
    "temperature":       0.0,    # ADR: default deterministic — change explicitly for stochastic runs
    "seed":              42,     # stored per-run for replay; fixed seed + temp=0 → deterministic
    "agent_id":          "external",
    "confidence_score":  0.75,
    "consistency_probe": False,   # ADR-06 mitigation — run query twice, compare conclusions
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ticker(message: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", (message.split() or ["CHAT"])[0])
    return (cleaned.upper()[:10]) or "CHAT"


def _canonicalize(text: str) -> str:
    """
    Normalise input text before prompt assembly.
    Ensures identical prompts are sent for identical logical inputs,
    regardless of whitespace or Unicode encoding differences.
    Week 6 / determinism: this is a prerequisite for seed-based replay to be meaningful.
      - Unicode NFC normalisation (e.g. composed vs decomposed accents → same bytes)
      - Strip leading/trailing whitespace
      - Collapse runs of spaces/tabs to a single space (preserves newlines)
    """
    text = unicodedata.normalize("NFC", text)
    text = text.strip()
    text = re.sub(r"[ \t]+", " ", text)   # collapse horizontal whitespace only
    return text


def _classify(score: float) -> ConfidenceClassification:
    return (
        ConfidenceClassification.HIGH_UNCERTAINTY
        if score < 0.4
        else ConfidenceClassification.STANDARD
    )


_URL_RE = re.compile(r"https?://\S+")


def _tool_event_detail(evt: dict) -> str | None:
    """
    Render a tool-call step's detail line: what the agent searched for, and
    (once the call finishes) a preview of what it got back. Without the
    query, the Run Timeline showed a search happened but never what it was
    a search *for* — the one thing a reader actually wants to audit.
    """
    parts = []
    if evt.get("query"):
        parts.append(f"query: {evt['query']!r}")
    if evt.get("result_preview"):
        parts.append(evt["result_preview"])
    return " · ".join(parts) or None


def _lens_facts(lens, facts: dict) -> list[dict]:
    """A lens's structured facts for the payload; a missing concept stays visible."""
    return [
        fact.to_dict() if fact is not None else {"concept": concept, "missing": True}
        for concept, fact in zip(lens.concepts, lens.select(facts))
    ]


def _tool_step_extra(evt: dict) -> dict:
    """Structured fields for a tool-call step, so the UI never parses the label."""
    return {
        "kind": "tool",
        "tool": evt.get("tool"),
        "tool_phase": evt.get("phase"),
        "query": evt.get("query"),
        "args": evt.get("args") or {},
        "retried_query_only": bool(evt.get("retried_query_only")),
        "urls": evt.get("urls") or [],
        # BP: per-result title and snippet — what the agent read at each URL.
        "results": evt.get("results") or [],
    }


def _tool_event_recorder(trace: StepTrace, phase: str):
    """Turn langchain_adapter.py's tool events into steps on `trace`."""
    return lambda evt: trace.note(
        phase,
        f"Tool call: {evt['tool']} ({evt['phase']})",
        url=evt.get("url"),
        detail=_tool_event_detail(evt),
        status=evt.get("status", "ok"),
        duration_ms=evt.get("duration_ms"),
        extra=_tool_step_extra(evt),
    )


# ── Live event streams (/api/chat/stream, /api/compare/stream) ─────────────────
# The run itself is the same synchronous function the plain routes call; it
# executes on a worker thread and pushes events through `emit`, which hops them
# onto the event loop via call_soon_threadsafe. Ordering is preserved: every
# emit from the worker is scheduled before asyncio.to_thread's own completion
# callback, so "result" is always the last event before the stream closes.
#
# A client disconnect ends the *stream*, not the *run*: the runner task keeps
# going and persists the record exactly as the plain route would. The record is
# the evidence; a closed browser tab must not be able to delete it.

Emit = Callable[[str, dict], None]

_STREAM_DONE = object()
_BACKGROUND_RUNS: set[asyncio.Task] = set()


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _event_stream(work: Callable[[Emit], dict]) -> StreamingResponse:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(event: str, data: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    async def runner() -> None:
        try:
            payload = await asyncio.to_thread(work, emit)
            await queue.put(("result", payload))
        except Exception as exc:
            await queue.put(("error", {"error": f"{type(exc).__name__}: {exc}"}))
        finally:
            await queue.put((_STREAM_DONE, None))

    task = asyncio.create_task(runner())
    _BACKGROUND_RUNS.add(task)          # a bare create_task can be garbage-collected mid-run
    task.add_done_callback(_BACKGROUND_RUNS.discard)

    async def body():
        while True:
            event, data = await queue.get()
            if event is _STREAM_DONE:
                return
            yield _sse(event, data)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _tool_capability_warning(provider: str, *texts: str) -> str | None:
    """
    None unless search genuinely can't help here right now: a URL is sitting
    right in the input (a cheap, imperfect proxy for "this needs a live
    check") and Tavily isn't configured. Surfaced so a user doesn't silently
    get an answer reasoned from the model's own training data when they
    specifically handed it something to check — exactly what prompted this
    (see logs/RUN_LOG.md's Mad Max entry). ProviderSpec.supports_tools() is a
    callable, not a fixed bool, because it depends on live environment state
    (TAVILY_API_KEY), not which provider was picked — there is only one now.
    """
    if resolve_provider(provider).supports_tools():
        return None
    if not any(_URL_RE.search(t or "") for t in texts):
        return None
    return (
        "Context references a URL, but Tavily search isn't configured "
        "(no TAVILY_API_KEY set) — this can't be checked live, only guessed "
        "from the model's own training data. Set TAVILY_API_KEY to enable live search."
    )


def _degrade_confidence(base: float, data_sources: tuple[DataSource, ...]) -> float:
    penalty = sum(
        0.1 for ds in data_sources
        if ds.status in (DataSourceStatus.SIMULATED, DataSourceStatus.FAILED)
    )
    return round(max(0.05, base - penalty), 2)


# Adapter construction, model labelling and per-producer overrides all live in
# adapters/registry.py. This route module used to carry its own copy of each, which
# meant the provider set was restated here every time a provider was added; see that
# module's docstring for the six places one fact used to be written in.
_producer_config = with_model_override
_model_label = model_label
_build_adapter = build_adapter
_build_model_call = build_model_call


# ── Pydantic request models ────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    context: str = ""


class ConfigUpdate(BaseModel):
    # No "provider" field — LangChain is the only framework, fixed in _config,
    # not user-selectable. "model" stays: it's how the caller picks WHICH
    # model LangChain drives (Ollama- or Gemini-family, inferred by name),
    # which is a verification-layer-relevant knob (it's recorded as evidence
    # per-run) even though "LLM settings" broadly now live outside this UI.
    model:             Optional[str]   = None
    temperature:       Optional[float] = None
    seed:              Optional[int]   = None
    agent_id:          Optional[str]   = None
    confidence_score:  Optional[float] = None
    consistency_probe: Optional[bool]  = None


class TokenRequest(BaseModel):
    scope: Literal["auditor", "investor"]


class CompareRequest(BaseModel):
    # Exactly one of these two selects the mode. "ticker" is the original,
    # EDGAR-backed financial path (Producer A/B, real SEC data, deliberately
    # information-asymmetric lenses). "subject" is the generic, domain-agnostic
    # path added in response to a live test asking two agents to verify a movie
    # fact (see logs/RUN_LOG.md) — both agents get the SAME subject and context
    # (there is no EDGAR-equivalent "second lens" for an arbitrary subject), so
    # any disagreement between them is meaningful on its own.
    ticker:  Optional[str] = None
    subject: Optional[str] = None
    context: str = ""
    # Per-producer model overrides, mirroring scripts/run_cross_agent_live.py's
    # --agent-a-model / --agent-b-model. Unset means "use the shared /api/config
    # model for that side". No provider field — there is only one framework;
    # a model name alone (Ollama- or Gemini-family, inferred by name) is
    # enough to give the two producers genuinely different models.
    agent_a_model: Optional[str] = None
    agent_b_model: Optional[str] = None
    # Which rule decides contradiction_flag. Unset = the mode's default:
    # concept_aware for ticker (canonical_facts is opt-in there — see
    # validation/cross_validation.py's ContradictionRule note), canonical_facts for
    # subject. The figure-by-figure rows are computed either way.
    contradiction_rule: Optional[Literal["concept_aware", "canonical_facts", "symmetric_difference"]] = None
    # B4, ticker mode only: which two lenses. "lenses" = financial vs earnings (each
    # sees part of the filing); "bull_bear" = the same figures, argued both ways.
    pairing: Literal["lenses", "bull_bear"] = "lenses"

    @model_validator(mode="after")
    def _exactly_one_mode(self):
        if bool(self.ticker) == bool(self.subject):
            raise ValueError(
                'Provide exactly one of "ticker" (financial, EDGAR-backed) or '
                '"subject" (generic, any domain) — not both, not neither.'
            )
        return self


class DecisionRequest(BaseModel):
    # Shape only; the rules (known figure, rationale length, value only for
    # override_value...) live in validation/gate.py's validate_decision, so the
    # database CHECKs, the route and the tests all enforce one definition.
    decision:    str
    decided_by:  str
    rationale:   str
    cited_items: list[str]
    final_value: Optional[float] = None
    final_grade: Optional[str] = None


class FlagRequest(BaseModel):
    flag_type:     Literal["Hallucinated", "Incorrect", "Other"]
    reviewer_note: Optional[str] = None


# ── Auth routes ────────────────────────────────────────────────────────────────

@app.post("/api/auth/token")
async def get_token(body: TokenRequest):
    """
    SEC-02: Issue a JWT for the requested scope.

    TODO (production): verify caller identity before issuing auditor tokens.
    For Phase 1 prototype any caller can self-serve any scope.
    """
    token = issue_token(body.scope)
    return {"access_token": token, "token_type": "bearer", "scope": body.scope}


# ── Static / config routes ─────────────────────────────────────────────────────

@app.get("/")
async def root():
    """
    The React app (U9 cutover, 2026-09-27). Without a build there is nothing to
    serve, so say how to make one rather than show a blank page.
    """
    # no-store: the classic UI at "/" was a plain file response that browsers cached
    # without revalidating, and a cached copy outlived the cutover (seen 2026-09-27).
    # Whatever "/" becomes next, nobody should be left on a stale copy of it.
    if FRONTEND_DIST.is_dir():
        return RedirectResponse("/app/", headers={"Cache-Control": "no-store"})
    return HTMLResponse(
        "<!doctype html><title>Verification Layer</title><h1>The UI isn't built yet</h1>"
        "<p>Run <code>npm ci &amp;&amp; npm run build</code> in <code>web/frontend/</code> "
        "(or <code>scripts/start-server.sh</code>, which does it), then reload. The API is up: "
        "<a href=\"/docs\">/docs</a>.</p>",
        status_code=503, headers={"Cache-Control": "no-store"},
    )


@app.get("/api/config")
async def get_config():
    return _config


@app.post("/api/config")
async def update_config(update: ConfigUpdate):
    if update.model is not None:
        _config["model"] = update.model
    if update.temperature is not None:
        _config["temperature"] = round(update.temperature, 2)
    if update.seed is not None:
        _config["seed"] = update.seed
    if update.agent_id is not None:
        try:
            AgentID(update.agent_id)
        except ValueError:
            raise HTTPException(400, detail=f"Unknown agent_id: {update.agent_id!r}")
        _config["agent_id"] = update.agent_id
    if update.confidence_score is not None:
        _config["confidence_score"] = round(
            max(0.0, min(1.0, update.confidence_score)), 2
        )
    if update.consistency_probe is not None:
        _config["consistency_probe"] = update.consistency_probe
    return _config


# ── Chat route (SEC-02: scope from JWT) ───────────────────────────────────────

@app.post("/api/chat")
def chat(
    request: ChatRequest,
    scope: ScopeT = Depends(require_scope),
):
    """
    Execute one agent run through the full accountability loop.

    SEC-02: scope is read from the JWT Bearer token, NOT from a query param.
    scope=auditor  → full record including thought_log (internal tier)
    scope=investor → thought_log, raw_output, llm_tokens structurally excluded (SEC-01)

    A plain `def` on purpose: the run blocks for as long as the model takes, and
    FastAPI executes sync routes on its threadpool. As `async def` it ran on the
    event loop itself and stalled every other request until the model returned.
    """
    return _run_chat(request, scope)


@app.post("/api/chat/stream")
async def chat_stream(
    request: ChatRequest,
    scope: ScopeT = Depends(require_scope),
):
    """Same run as /api/chat, streamed: step events as they happen, then `result`."""
    return _event_stream(lambda emit: _run_chat(request, scope, emit=emit))


def _run_chat(request: ChatRequest, scope: ScopeT, emit: Emit | None = None) -> dict:
    run_id       = uuid.uuid4()
    agent_id     = AgentID(_config["agent_id"])
    directive    = get_active_directive()
    investor     = (scope == "investor")

    # Chronological record of everything that happens this run — every LLM
    # attempt (via wrap_adapter, including a retry's corrective directive) and
    # every tool call the agent makes mid-reasoning (via _on_tool_event,
    # langchain_adapter.py's callback — see its module docstring). "_on_tool_event"
    # is injected into a per-call COPY of _config, never the shared dict itself,
    # so the callable never reaches config_snapshot's json.dumps below.
    trace = StepTrace()
    if emit is not None:
        trace.subscribe(emit)
        emit("run_started", {"run_id": str(run_id), "mode": "chat", "subject": request.message})
    call_cfg = dict(_config)
    call_cfg["_on_tool_event"] = _tool_event_recorder(trace, PHASE_CHAT)
    adapter = wrap_adapter(
        trace, PHASE_CHAT, _build_adapter(call_cfg), model_label=_model_label(_config)
    )

    # Canonicalize inputs before prompt assembly — prerequisite for seed-based
    # replay to be meaningful. Same logical input must produce the same prompt bytes.
    canonical_message = _canonicalize(request.message)
    canonical_context = _canonicalize(request.context)

    # /api/chat has no real data source of its own to report (unlike /api/compare,
    # which fetches live EDGAR data) — no simulated stand-in is substituted here.
    data_sources: tuple[DataSource, ...] = ()

    confidence      = _degrade_confidence(_config["confidence_score"], data_sources)
    degraded        = confidence < _config["confidence_score"]
    classification  = _classify(confidence)

    payload: dict = {
        "run_id":                    str(run_id),
        "subject":                   canonical_message,
        "scope":                     scope,
        "halted":                    False,
        "conclusion":                None,
        "thought_log":               None,
        "confidence_score":          confidence,
        "confidence_degraded":       degraded,
        "confidence_classification": classification.value,
        "high_uncertainty":          classification == ConfidenceClassification.HIGH_UNCERTAINTY,
        "data_sources": [
            {
                "source":          ds.source,
                "status":          ds.status.value,
                "url":             ds.url,
                "provenance_note": ds.provenance_note,
            }
            for ds in data_sources
        ],
        "reasoning_objects":  [],
        "session":            None,
        "error":              None,
        "config_snapshot":    dict(_config),
        "steps":              [],  # chronological trace — LLM attempts + MCP tool calls; filled in below
        "tool_capability_warning": _tool_capability_warning(
            _config["provider"], canonical_message, canonical_context
        ),
        # ADR-06 mitigations — populated after successful run
        "claims":              [],    # structured claims extracted from thought_log
        "consistency":         None,  # consistency probe result (if enabled)
        "verification_rate":   None,  # fraction of citation claims confirmed against source
    }

    initiated_at = datetime.now(timezone.utc)

    try:
        result = run_validation_loop(
            canonical_message,
            canonical_context,
            run_id,
            agent_id,
            confidence_score=confidence,
            data_sources=data_sources,
            call_agent_fn=adapter,
        )

        session = RunSession(
            ticker=_ticker(request.message),
            directive_version=directive.version,
            directive_text=directive.text,
            run_id=run_id,
            initiated_at=initiated_at,
            status=RunStatus.COMPLETE,
            reasoning_objects=tuple(result.reasoning_objects),
            run_confidence_score=confidence,
            confidence_classification=classification,
            completed_at=datetime.now(timezone.utc),
        )

        payload["reasoning_objects"] = [
            ro.to_dict(investor_scope=investor) for ro in result.reasoning_objects
        ]
        payload["session"] = session.to_dict()

        if result.final_response:
            payload["conclusion"]  = result.final_response.conclusion
            payload["thought_log"] = None if investor else result.final_response.thought_log

            # ── ADR-06: claim extraction + verification ────────────────────────
            if result.final_response.thought_log or result.final_response.conclusion:
                raw_claims = extract_claims_from_response(
                    result.final_response.thought_log, result.final_response.conclusion
                )
                verified_claims, v_rate = verify_claims(raw_claims)
                payload["claims"]            = [c.to_dict() for c in verified_claims]
                payload["verification_rate"] = v_rate

            # ── ADR-06: consistency probe — auto-on for Ollama-family models
            # (their determinism claim is documented-unreliable, see
            # logs/RUN_LOG.md's ollama-determinism entry), opt-in otherwise.
            # There's no separate "ollama provider" to check anymore — model
            # family is inferred from the name, same as langchain_adapter.py.
            probe_enabled = (
                _config.get("consistency_probe")
                or "gemini" not in _config.get("model", "").lower()
            )
            if probe_enabled and result.final_response.conclusion:
                probe = run_consistency_probe(
                    subject=canonical_message,
                    context=canonical_context,
                    agent_id=agent_id,
                    confidence_score=confidence,
                    data_sources=data_sources,
                    call_agent_fn=adapter,
                    primary_conclusion=result.final_response.conclusion,
                )
                payload["consistency"] = probe.to_dict()

    except HaltError as exc:
        session = RunSession(
            ticker=_ticker(request.message),
            directive_version=directive.version,
            directive_text=directive.text,
            run_id=run_id,
            initiated_at=initiated_at,
            status=RunStatus.HALTED,
            reasoning_objects=tuple(exc.reasoning_objects),
            completed_at=datetime.now(timezone.utc),
        )
        payload["halted"]            = True
        payload["error"]             = str(exc)
        payload["reasoning_objects"] = [
            ro.to_dict(investor_scope=investor) for ro in exc.reasoning_objects
        ]
        payload["session"] = session.to_dict()

    except EnvironmentError as exc:
        payload["halted"] = True
        payload["error"]  = f"Configuration error: {exc}"

    except LangchainConnectionError as exc:
        payload["halted"] = True
        payload["error"]  = f"⚠ Model or search tool unreachable: {exc}"

    except Exception as exc:
        payload["halted"] = True
        payload["error"]  = f"Adapter error — {type(exc).__name__}: {exc}"

    # Every branch above except the success and HaltError paths leaves
    # payload["session"] at its None default. insert_run() requires
    # initiated_at, which only ever comes from session.to_dict() — without this,
    # any adapter-level failure (Ollama unreachable, rate limit, bad config, ...)
    # would crash insert_run() with a NOT NULL IntegrityError, turning a clean,
    # readable error message into an opaque 500. A minimal HALTED session with
    # no reasoning_objects is enough to make the run insertable and honest about
    # the fact it never produced one.
    if payload["session"] is None:
        session = RunSession(
            ticker=_ticker(request.message),
            directive_version=directive.version,
            directive_text=directive.text,
            run_id=run_id,
            initiated_at=initiated_at,
            status=RunStatus.HALTED,
            completed_at=datetime.now(timezone.utc),
        )
        payload["session"] = session.to_dict()

    # Chronological trace of every LLM attempt and MCP tool call this run made,
    # regardless of outcome — set unconditionally, same as the session fallback
    # above, so a halted or errored run still carries whatever happened before
    # the failure instead of losing it.
    payload["steps"] = trace.to_list()

    # C-03: persist to SQLite
    insert_run(payload)
    if payload["session"]:
        insert_session(str(run_id), _ticker(request.message), payload["session"])

    return payload


# ── Cross-Agent Validation routes ──────────────────────────────────────────────
# Closes SDD §14's explicitly-deferred "any new HTTP route" item. Producer A
# (producers/financial.py) and Producer B (producers/earnings.py) both read the same live
# EDGAR companyfacts payload for the ticker, through different concept slices —
# see producers/earnings.py's docstring for why that's real information asymmetry
# without requiring a second data provider.
#
# SEC-02: same JWT-scope gate as /api/chat.
# SEC-01 note (read before touching this route): the top-level `reasoning_objects`
# below is redacted per the caller's actual scope, exactly like /api/chat's is —
# NOT by trusting whatever cross_validation.persist_cross_agent_run() wrote to the
# store, which is unconditionally full/auditor-scope (build_run_payload() hardcodes
# investor_scope=False, both at the top level and inside its nested `session`
# object — the same class of bug as accountability-layer-audit.md's CRITICAL #1,
# just in validation/cross_validation.py rather than RunSession.to_dict()). This route does
# NOT fix that stored-data bypass — fixing it means touching shared code
# (build_run_payload / RunSession.to_dict) this change was scoped not to modify.
# It is exactly as secure as every other route today: not worse, not fixed.

@app.post("/api/compare")
def compare(
    request: CompareRequest,
    scope: ScopeT = Depends(require_scope),
):
    """
    Run Cross-Agent Validation over HTTP, in one of two modes (see CompareRequest):

    ticker mode (financial) — Producer A (producers/financial.py) vs. Producer B
    (producers/earnings.py) on the same ticker, deliberately information-
    asymmetric EDGAR concept slices, using the configured provider/adapter for
    both sides (see /api/config — this route does not accept a per-producer
    provider override; that's what scripts/run_cross_agent_live.py is for).

    subject mode (generic, any domain) — two independent agents given the SAME
    subject and context (there is no EDGAR-equivalent second lens for an
    arbitrary subject), each free to use their own tools (e.g. the MCP fetch
    tool — see adapters/langchain_adapter.py) to verify it. Added after a live
    test asked whether a movie was released in a given year and found the
    layer had no non-financial path at all — see logs/RUN_LOG.md.

    A plain `def` for the same reason as /api/chat: FastAPI runs it on the
    threadpool instead of blocking the event loop for the whole comparison.
    """
    return _run_compare(request, scope)


@app.post("/api/compare/stream")
async def compare_stream(
    request: CompareRequest,
    scope: ScopeT = Depends(require_scope),
):
    """
    Same comparison as /api/compare, streamed. Events: run_started (with producer
    metadata), step_started / step_finished (shared fetch, each agent's LLM
    attempts and tool calls, interleaved when the agents run concurrently),
    agent_finished (per agent, as each one completes), result (the exact
    payload /api/compare would have returned), or error.
    """
    return _event_stream(lambda emit: _run_compare(request, scope, emit=emit))


def _run_compare(request: CompareRequest, scope: ScopeT, emit: Emit | None = None) -> dict:
    is_financial = request.ticker is not None
    run_id   = uuid.uuid4()
    investor = (scope == "investor")

    cfg_a = _producer_config(_config, request.agent_a_model)
    cfg_b = _producer_config(_config, request.agent_b_model)
    trace = StepTrace()
    if emit is not None:
        # An investor caller never gets search-result text mid-run: whether the gate
        # will withhold the run isn't known until both agents are compared (the same
        # reason agent_finished carries no conclusion for them).
        trace.subscribe(emit if not investor else
                        lambda event, step: emit(event, strip_search_content(step)))

    # Every tool call either side makes mid-reasoning is reported into the same
    # chronological trace as the LLM attempts — same convention /api/chat's
    # StepTrace wiring uses, so a run's timeline is genuinely everything that
    # happened, not just the two final conclusions. cfg_a/cfg_b are already
    # fresh per-call dicts from with_model_override, so mutating them directly
    # (rather than copying again) is safe — neither is serialized into this
    # route's payload the way /api/chat's config_snapshot would be.
    cfg_a["_on_tool_event"] = _tool_event_recorder(trace, PHASE_AGENT_A)
    cfg_b["_on_tool_event"] = _tool_event_recorder(trace, PHASE_AGENT_B)

    if is_financial:
        ticker = request.ticker.upper().strip()
        lens_a, lens_b = PAIRINGS[request.pairing]
        agent_a_id, agent_b_id = lens_a.agent_id, lens_b.agent_id
        roles = ("PRODUCER A", "PRODUCER B") if request.pairing == "lenses" else ("BULL", "BEAR")
        producers_meta = {
            "a": {
                "agent_id": agent_a_id.value, "role": roles[0], "lens": lens_a.name,
                "model": _model_label(cfg_a), "concepts": list(lens_a.concepts),
                "lens_version": lens_a.version,
                "overridden": bool(request.agent_a_model),
            },
            "b": {
                "agent_id": agent_b_id.value, "role": roles[1], "lens": lens_b.name,
                "model": _model_label(cfg_b), "concepts": list(lens_b.concepts),
                "lens_version": lens_b.version,
                "overridden": bool(request.agent_b_model),
            },
            "pairing": request.pairing,
            # B2: the figures both agents are handed, from the lens definitions.
            "shared_concepts": sorted(lens_shared_concepts(lens_a, lens_b)),
            "same_model": _model_label(cfg_a) == _model_label(cfg_b),
        }
    else:
        ticker = None
        subject = request.subject.strip()
        agent_a_id, agent_b_id = AgentID.GENERIC_A, AgentID.GENERIC_B
        # Concepts stay empty here on purpose — "concept_aware" contradiction
        # tagging (validation/concept_linkage.py) is keyword-matched against a
        # fixed financial vocabulary (Assets, Revenues, EPS, ...) and would
        # never tag anything for an arbitrary subject; the symmetric_difference
        # rule below is the one that actually applies to this mode.
        producers_meta = {
            "a": {
                "agent_id": agent_a_id.value, "role": "AGENT A",
                "model": _model_label(cfg_a), "concepts": [],
                "overridden": bool(request.agent_a_model),
            },
            "b": {
                "agent_id": agent_b_id.value, "role": "AGENT B",
                "model": _model_label(cfg_b), "concepts": [],
                "overridden": bool(request.agent_b_model),
            },
            "same_model": _model_label(cfg_a) == _model_label(cfg_b),
        }

    payload: dict = {
        "run_id":                 str(run_id),
        "ticker":                 ticker,
        "subject":                None if is_financial else subject,
        "scope":                  scope,
        "halted":                 False,
        "cross_agent_comparison": None,
        "reasoning_objects":      [],
        "session":                None,
        "error":                  None,
        "claims":                 {"a": [], "b": []},
        "verification_rate":      {"a": None, "b": None},
        # Setup metadata for the UI. Concept lists come from the grader modules
        # themselves — the UI must not hard-code them, or it silently lies the
        # moment either module's concept tuple changes.
        "producers": producers_meta,
        # The verbatim strings each producer was handed, so the UI can show what an
        # agent actually saw next to what it cited.
        "contexts": {"a": None, "b": None},
        # The same inputs as structured facts (value, unit, period, form, frame, accn)
        # so the UI can align them without parsing the context strings. Financial
        # mode only; generic mode has no filing data.
        "facts": {"a": [], "b": []},
        "steps":    [],
        # One provider now, so one check — cfg_a/cfg_b would resolve identically.
        "tool_capability_warning": _tool_capability_warning(_config["provider"], request.context or ""),
    }

    if emit is not None:
        emit("run_started", {
            "run_id": str(run_id),
            "mode": "ticker" if is_financial else "subject",
            "ticker": ticker,
            "subject": payload["subject"],
            "producers": producers_meta,
        })

    try:
        if is_financial:
            with trace.record(
                PHASE_SHARED, "lookup_cik",
                url="https://www.sec.gov/files/company_tickers.json",
                extra={"kind": "fetch"},
            ) as step:
                cik = lookup_cik(ticker)
                step["detail"] = f"{ticker} -> CIK {cik}"

            facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            with trace.record(
                PHASE_SHARED, "fetch_company_facts", url=facts_url,
                detail="one payload, reused by BOTH producers",
                extra={"kind": "fetch", "cik": cik},
            ):
                facts = fetch_company_facts(ticker, cik)

            # Step labels keep their pre-B4 names for the original pairing, so stored
            # traces and the UI read the same; `slot` says whose inputs each step made.
            for slot, lens in (("a", lens_a), ("b", lens_b)):
                label = {"financial": "summarize_facts", "earnings": "summarize_earnings_facts"}.get(
                    lens.name, f"summarize_{lens.name}_facts")
                with trace.record(
                    PHASE_SHARED, label,
                    detail=f"{roles[0 if slot == 'a' else 1].title()} lens: {', '.join(lens.concepts)}",
                    extra={"slot": slot},
                ):
                    if slot == "a":
                        context_a = lens.summarize(ticker, facts)
                    else:
                        context_b = lens.summarize(ticker, facts)

            payload["facts"] = {
                "a": _lens_facts(lens_a, facts),
                "b": _lens_facts(lens_b, facts),
            }

            data_sources_a = data_sources_b = (
                DataSource(source="SEC EDGAR", status=DataSourceStatus.LIVE, url=facts_url),
            )
            # producers/financial.py vs. producers/earnings.py are deliberately
            # information-asymmetric (different EDGAR concept sets). concept_aware
            # (2026-09-11) measures best on the real-run corpus (see
            # tests/test_concept_linkage.py); canonical_facts is opt-in here until a
            # corpus that records contexts can adjudicate the ratios it flags.
            # Bull and bear see identical figures, so any figure both cite is comparable
            # and one they both skip isn't a conflict: the figure-by-figure rule fits.
            contradiction_rule = request.contradiction_rule or (
                "concept_aware" if request.pairing == "lenses" else "canonical_facts")
            include_years = False
            run_subject = ticker
            shared = lens_shared_concepts(lens_a, lens_b)
            source_payload = facts
        else:
            # Both agents get the identical subject and context — there is no
            # EDGAR-equivalent second lens for an arbitrary subject, so the
            # only asymmetry left is each agent's own model and tool use.
            context_a = context_b = request.context or ""
            trace.note(
                PHASE_SHARED, "shared context",
                detail="both agents receive the identical subject and context — no per-agent data slice in generic mode",
            )

            data_sources_a = data_sources_b = ()
            # Both agents saw the same input and answer the same question, so a
            # figure one states and the other omits is meaningful. canonical_facts
            # (with years) compares by what each figure is — the old
            # symmetric_difference couldn't see a bare year at all, so "released in
            # 2010" vs "2015" passed as no contradiction.
            contradiction_rule = request.contradiction_rule or "canonical_facts"
            include_years = True
            run_subject = subject
            shared = frozenset()
            source_payload = None

        payload["contexts"] = {"a": context_a, "b": context_b}

        # Wrapped so each LLM attempt is its own timed step, including the directive
        # version — which is how ADR-07's corrective retry becomes visible.
        adapter_a = wrap_adapter(
            trace, PHASE_AGENT_A, _build_adapter(cfg_a), model_label=_model_label(cfg_a)
        )
        adapter_b = wrap_adapter(
            trace, PHASE_AGENT_B, _build_adapter(cfg_b), model_label=_model_label(cfg_b)
        )

        # B4, option 1: in ticker mode, one more call per agent reads its finished answer
        # for a grade/direction (a model judgment). Generic subjects have no company
        # to grade, so they get none.
        assess_a = assess_b = None
        if is_financial:
            assess_a = wrap_model_call(trace, PHASE_AGENT_A, _build_model_call(cfg_a),
                                       model_label=_model_label(cfg_a), prompt_version=EXTRACTION_PROMPT_VERSION)
            assess_b = wrap_model_call(trace, PHASE_AGENT_B, _build_model_call(cfg_b),
                                       model_label=_model_label(cfg_b), prompt_version=EXTRACTION_PROMPT_VERSION)

        result, objects = run_cross_agent_validation(
            run_subject,
            context_a,
            context_b,
            agent_a_id,
            agent_b_id,
            adapter_a,
            adapter_b,
            run_id=run_id,
            data_sources_a=data_sources_a,
            data_sources_b=data_sources_b,
            contradiction_rule=contradiction_rule,
            include_years=include_years,
            shared_concepts=shared,
            given_facts=payload["facts"],
            source_payload=source_payload,
            assess_a_fn=assess_a,
            assess_b_fn=assess_b,
            on_agent_finished=(
                None if emit is None else
                lambda slot, conclusion, halted: emit(
                    # Investor callers get no conclusion mid-run: whether the gate
                    # will withhold it isn't known until both agents are compared.
                    "agent_finished", {"agent": slot, "halted": halted,
                                       "conclusion": None if investor else conclusion}
                )
            ),
        )

        trace.note(
            PHASE_COMPARE, "compare conclusions",
            detail=f"numeric divergence only · status={result.status.value}"
                   f" · contradiction_rule={contradiction_rule}",
            extra={"kind": "compare"},
        )

        payload["halted"]                 = result.status != ComparisonStatus.COMPARED
        payload["cross_agent_comparison"] = result.to_dict()
        # Redacted per the CALLER's scope now, not the scope baked into storage —
        # see the SEC-01 note above this route.
        payload["reasoning_objects"] = [
            ro.to_dict(investor_scope=investor) for ro in objects
        ]

        # ── ADR-06: claim extraction + verification, per producer ──────────────
        # /api/chat has done this since Week 7; /api/compare never did, which is
        # exactly why the AAPL 0.34 debt-to-equity fabrication was only ever
        # caught by a human reading the raw thought_log (web/self_report.py's
        # fabrication-not-caught entry). Runs against each producer's own
        # SUCCESS-status ReasoningObject, independently — a claim from Producer
        # A's thought_log is never checked against Producer B's citations.
        payload["claims"] = {"a": [], "b": []}
        payload["verification_rate"] = {"a": None, "b": None}
        for slot, agent_id in (("a", agent_a_id), ("b", agent_b_id)):
            successful = next(
                (
                    ro for ro in objects
                    if ro.agent_id == agent_id and ro.parse_status == ParseStatus.SUCCESS
                ),
                None,
            )
            if successful and (successful.thought_log or successful.conclusion):
                raw_claims = extract_claims_from_response(
                    successful.thought_log, successful.conclusion
                )
                verified_claims, v_rate = verify_claims(raw_claims)
                # Investor scope never sees thought_log itself (SEC-01); claims
                # extracted FROM it are evidence about the run, not the log's
                # raw content, but withheld too for the same reason the log is.
                payload["claims"][slot] = (
                    [] if investor else [c.to_dict() for c in verified_claims]
                )
                payload["verification_rate"][slot] = v_rate

        # Keep enough in the record that a run reopened from history can show its
        # inputs, trace and figure checks (claims as already redacted for `scope`).
        stored = persist_cross_agent_run(result, objects, scope=scope, extra={
            "ticker": payload["ticker"],
            "producers": payload["producers"],
            "contexts": payload["contexts"],
            "facts": payload["facts"],
            "claims": payload["claims"],
            "verification_rate": payload["verification_rate"],
            "steps": trace.to_list(),
            # BG: this run is subject to the decision gate. Runs stored without the
            # key predate it and are never gated retroactively (validation/gate.py).
            "gate_policy": GATE_POLICY,
        })
        payload["session"] = stored["session"]
        payload["gate_policy"] = GATE_POLICY
        payload["gate"] = gate_state(stored, [])

    except EdgarFetchError as exc:
        payload["halted"] = True
        payload["error"]  = f"EDGAR fetch failed: {exc}"

    except EnvironmentError as exc:
        payload["halted"] = True
        payload["error"]  = f"Configuration error: {exc}"

    except LangchainConnectionError as exc:
        payload["halted"] = True
        payload["error"]  = f"⚠ Model or search tool unreachable: {exc}"

    except Exception as exc:
        payload["halted"] = True
        payload["error"]  = f"Adapter error — {type(exc).__name__}: {exc}"

    # Attached last, and outside the try, so a run that failed partway still returns
    # the steps that did complete — a partial trace localises the failure, which is
    # exactly what you want when EDGAR or the model is the thing that broke.
    payload["steps"] = trace.to_list()
    # An investor caller gets the gate's withholding on the live response too, not
    # only when the run is read back from History.
    if investor and (payload.get("gate") or {}).get("status") == AWAITING_DECISION:
        payload = redact_for_scope(payload, "investor", payload["gate"])
    return payload


# -- Reads: every stored run goes out through the gate (BG) --------------------
# Reads have never required a token (the legacy UI sends none), so the scope a read
# is served at is the token's if one is sent, else the scope the run was stored
# under. That keeps the legacy UI working; it also means an unauthenticated reader
# can read at auditor scope any run an auditor created -- a pre-existing property of
# these routes, recorded in logs/RUN_LOG.md rather than silently changed here.

def _read_scope(token_scope: ScopeT | None, run: dict) -> str:
    return token_scope or run.get("scope") or "auditor"


def _serve_runs(runs: list[dict], token_scope: ScopeT | None) -> list[dict]:
    decisions = get_decisions_for([r["run_id"] for r in runs if r.get("gate_policy")])
    return [
        redact_for_scope(r, _read_scope(token_scope, r), gate_state(r, decisions.get(r["run_id"], [])))
        for r in runs
    ]


@app.get("/api/runs/contradictions")
def runs_contradictions(
    ticker: Optional[str] = Query(default=None, description="Filter by ticker symbol"),
    limit:  int           = Query(default=50, le=200),
    scope:  Optional[ScopeT] = Depends(optional_scope),
):
    """
    Closes SDD §7.3's "not independently queryable" gap: every persisted
    Cross-Agent Validation run whose comparison flagged a contradiction.
    Python-side scan over get_runs() (see cross_validation.list_contradictions
    docstring) — not a SQL WHERE clause, since cross_agent_comparison lives in
    the free-form payload JSON blob, not its own column. Fine at this scale;
    a dedicated column/index is the schema change SDD §7.3 defers, not
    reintroduced here.
    """
    return _serve_runs(list_contradictions(ticker=ticker, limit=limit), scope)


# ── Run query routes (C-04) ────────────────────────────────────────────────────

@app.get("/api/runs")
def list_runs(
    ticker: Optional[str] = Query(default=None, description="Filter by ticker symbol"),
    from_dt: Optional[str] = Query(default=None, alias="from", description="ISO-8601 start date"),
    to_dt:   Optional[str] = Query(default=None, alias="to",   description="ISO-8601 end date"),
    limit:   int           = Query(default=50,   le=200),
    scope:   Optional[ScopeT] = Depends(optional_scope),
):
    return _serve_runs(get_runs(ticker=ticker, from_dt=from_dt, to_dt=to_dt, limit=limit), scope)


@app.get("/api/runs/drift")
async def runs_drift(
    ticker: str = Query(..., description="Ticker symbol to analyse"),
):
    """C-04: Return confidence scores over time for a given ticker."""
    results = get_drift(ticker)
    if not results:
        return {"ticker": ticker.upper(), "points": []}
    return {"ticker": ticker.upper(), "points": results}


@app.get("/api/runs/{run_id}")
def get_run_by_id(run_id: str, scope: Optional[ScopeT] = Depends(optional_scope)):
    run = get_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Run not found")
    return _serve_runs([run], scope)[0]


# -- Sources (BP) ---------------------------------------------------------------
# Where a figure came from, on demand. Every parameter below ends up in an SEC URL
# or a cache file name, so each is checked against its exact format first.

_CIK_RE = re.compile(r"\d{1,10}")
_ACCN_RE = re.compile(r"\d{10}-\d{2}-\d{6}")
_CONCEPT_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{1,120}")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@app.get("/api/facts/excerpt")
def fact_excerpt(
    cik: str = Query(...), accn: str = Query(...), concept: str = Query(...),
    end: str = Query(...), start: Optional[str] = Query(default=None),
):
    """
    BP: the table row (or paragraph) a companyfacts figure sits in, in its filing's
    inline XBRL (datasources/filings.py). Lazy — nothing is fetched until asked —
    and it never 500s on an odd filing: `status` says what happened
    (found / not_found / no_inline_xbrl / too_large / fetch_failed / not_in_index),
    with the filing's index URL either way so "Open filing" always works.
    """
    bad = [name for name, value, pattern in (
        ("cik", cik, _CIK_RE), ("accn", accn, _ACCN_RE), ("concept", concept, _CONCEPT_RE),
        ("end", end, _DATE_RE), ("start", start, _DATE_RE),
    ) if value is not None and not pattern.fullmatch(value)]
    if bad:
        raise HTTPException(422, detail=f"Malformed parameter(s): {', '.join(bad)}")
    return find_excerpt(cik, accn, concept, start, end).to_dict()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


@app.get("/api/runs/{run_id}/source-snippet")
def source_snippet(
    run_id: str, url: str = Query(...), figure: Optional[str] = Query(default=None),
    scope: Optional[ScopeT] = Depends(optional_scope),
):
    """
    BP, for web citations: the snippet a search returned for `url` in this run, as
    the agent read it (recorded at the time — the page is not re-fetched). With
    `figure`, the span of that figure in the snippet, if it appears there.
    Runs recorded before 2026-09-25 kept no per-result snippets; they say so.
    """
    run = get_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Run not found")
    if _read_scope(scope, run) == "investor" and \
            gate_state(run, get_decisions(run_id))["status"] == AWAITING_DECISION:
        # Same rule as the run itself: search text is withheld while the gate is open.
        return {"status": "withheld", "url": url,
                "message": "Withheld: pending human review. The agents disagree on a figure and no reviewer has decided yet."}
    steps = run.get("steps") or []
    for step in steps:
        for r in step.get("results") or []:
            if r.get("url") != url:
                continue
            snippet = r.get("snippet") or ""
            span = None
            if figure and snippet:
                at = snippet.find(figure)
                if at < 0 and _digits(figure):
                    # "$94.9 billion" vs "94.9 billion": match on the number itself.
                    m = re.search(re.escape(re.sub(r"^[^\d]+", "", figure).split()[0]), snippet)
                    at = m.start() if m else -1
                    span = (at, m.end()) if m else None
                else:
                    span = (at, at + len(figure)) if at >= 0 else None
            agent = {"agent_a": "a", "agent_b": "b"}.get(step.get("phase"), step.get("phase"))
            return {"status": "found", "url": url, "title": r.get("title"), "snippet": snippet,
                    "highlight": span, "query": step.get("query"), "agent": agent}
    recorded = any("results" in s for s in steps)
    return {"status": "not_found", "url": url,
            "message": ("No search in this run returned that URL." if recorded else
                        "This run was recorded before search snippets were kept, so only the URL is known.")}


# -- Audit export (B6) ----------------------------------------------------------
# A view of the stored record, never a change to it, served through the same
# redaction as the run itself: an investor's export carries no more than an
# investor's read.

def _audit_for(run_id: str, scope: Optional[ScopeT]) -> tuple[dict, str]:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Run not found")
    if "cross_agent_comparison" not in run:
        raise HTTPException(422, detail="Only compare runs have an audit record.")
    read_scope = _read_scope(scope, run)
    served = _serve_runs([run], scope)[0]
    return audit_record(served, scope=read_scope), read_scope


@app.get("/api/runs/{run_id}/audit")
def run_audit(run_id: str, scope: Optional[ScopeT] = Depends(optional_scope)):
    """The run's audit record as JSON (validation/audit.py)."""
    return _audit_for(run_id, scope)[0]


@app.get("/api/runs/{run_id}/export.md", response_class=PlainTextResponse)
def run_export_markdown(run_id: str, scope: Optional[ScopeT] = Depends(optional_scope)):
    """The same record as a Markdown review a person reads — the default export."""
    record, read_scope = _audit_for(run_id, scope)
    return PlainTextResponse(
        to_markdown(record), media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="review-{run_id[:8]}-{read_scope}.md"'},
    )


# -- Gate decisions (BG) -------------------------------------------------------

@app.get("/api/runs/{run_id}/decisions")
def list_decisions(run_id: str, scope: Optional[ScopeT] = Depends(optional_scope)):
    """The run's gate: status, which figures still need a decision, full history.
    Served through the same redaction as the run (a pending check's arithmetic
    states the disputed figure)."""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Run not found")
    return redact_for_scope(run, _read_scope(scope, run), gate_state(run, get_decisions(run_id)))["gate"]


@app.post("/api/runs/{run_id}/decisions")
def record_decision(
    run_id: str,
    body: DecisionRequest,
    scope: ScopeT = Depends(require_scope),
):
    """
    P4: a named human clears the gate. Auditor scope only. Appends one decision
    (never edits one) and returns the gate as it now stands.
    """
    if scope != "auditor":
        raise HTTPException(403, detail="Only auditor-scoped tokens may record a decision.")
    run = get_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Run not found")
    try:
        fields = validate_decision(run, body.model_dump())
    except DecisionError as exc:
        raise HTTPException(422, detail=str(exc))
    insert_decision(run_id, fields)
    return gate_state(run, get_decisions(run_id))


@app.post("/api/runs/{run_id}/replay")
def replay_run(run_id: str):  # sync on purpose: a model call; runs on the threadpool, not the event loop
    """
    Week 6 — Determinism: re-run a historical run with its exact stored
    config (model, temperature, seed, directive) and diff the conclusion.

    The replay uses the original canonical subject and context stored in the
    run record. The result is NOT persisted — it is metadata for auditors to
    verify that the same inputs produce the same output.

    Returns:
      original_conclusion  — conclusion from the stored run
      replay_conclusion    — conclusion from the fresh run
      match                — True if conclusions are byte-identical
      diff_chars           — character-level diff count (0 if match)
      original_config      — the config snapshot that produced the original
    """
    original = get_run(run_id)
    if original is None:
        raise HTTPException(404, detail="Run not found")

    payload     = original if isinstance(original, dict) else original
    subject     = payload.get("subject", "")
    config_snap = payload.get("config_snapshot", {})

    if not subject:
        raise HTTPException(400, detail="Run has no stored subject — cannot replay")

    # Reconstruct adapter from the stored config snapshot
    try:
        replay_adapter = _build_adapter(config_snap)
    except Exception as exc:
        raise HTTPException(400, detail=f"Cannot reconstruct adapter from stored config: {exc}")

    # Retrieve the directive version that was active for this run
    from core.directive import get_directive, get_active_directive
    directive_version = config_snap.get("directive_version") or payload.get("session", {}) \
        .get("directive_version") if isinstance(payload.get("session"), dict) else None

    try:
        directive = get_directive(directive_version) if directive_version else get_active_directive()
    except KeyError:
        directive = get_active_directive()

    original_conclusion = payload.get("conclusion")

    try:
        result = run_validation_loop(
            subject,
            "",          # context not stored separately — replay on subject only
            uuid.uuid4(),
            AgentID(config_snap.get("agent_id", "external")),
            confidence_score=config_snap.get("confidence_score", 0.75),
            directive=directive,
            call_agent_fn=replay_adapter,
        )
        replay_conclusion = result.final_response.conclusion if result.final_response else None
    except HaltError as exc:
        return {
            "run_id":             run_id,
            "original_conclusion": original_conclusion,
            "replay_conclusion":   None,
            "match":               False,
            "replay_halted":       True,
            "error":               str(exc),
            "original_config":     config_snap,
        }
    except Exception as exc:
        raise HTTPException(500, detail=f"Replay failed: {type(exc).__name__}: {exc}")

    match = original_conclusion == replay_conclusion
    diff_chars = sum(
        1 for a, b in zip(original_conclusion or "", replay_conclusion or "")
        if a != b
    ) + abs(len(original_conclusion or "") - len(replay_conclusion or ""))

    return {
        "run_id":              run_id,
        "original_conclusion": original_conclusion,
        "replay_conclusion":   replay_conclusion,
        "match":               match,
        "diff_chars":          diff_chars,
        "replay_halted":       False,
        "original_config":     config_snap,
    }


# ── Reviewer flags (UN-05) ─────────────────────────────────────────────────────

@app.post("/api/runs/{run_id}/flags")
async def flag_run(
    run_id: str,
    body: FlagRequest,
    scope: ScopeT = Depends(require_scope),
):
    """UN-05: Add a reviewer flag to a run. Never mutates the RunRecord itself."""
    if scope != "auditor":
        raise HTTPException(
            status_code=403,
            detail="Only auditor-scoped tokens may add reviewer flags.",
        )
    if get_run(run_id) is None:
        raise HTTPException(404, detail="Run not found")
    flag = insert_flag(run_id, body.flag_type, body.reviewer_note)
    return flag


@app.get("/api/runs/{run_id}/flags")
async def list_flags(run_id: str):
    """Return all reviewer flags for a run."""
    if get_run(run_id) is None:
        raise HTTPException(404, detail="Run not found")
    return get_flags(run_id)


# ── Session routes ─────────────────────────────────────────────────────────────

# A session nests the run's reasoning objects (conclusions included), so it is
# served through the same gate as the run it belongs to.

def _serve_session(session: dict, token_scope: ScopeT | None) -> dict:
    run = get_run(str(session.get("run_id"))) or {}
    scope = _read_scope(token_scope, run)
    if scope != "investor":
        return session
    gate = gate_state(run, get_decisions(run["run_id"])) if run else None
    return redact_for_scope({"session": session, "reasoning_objects": []}, scope, gate)["session"]


@app.get("/api/sessions")
def list_sessions(scope: Optional[ScopeT] = Depends(optional_scope)):
    return [_serve_session(s, scope) for s in get_sessions()]


@app.get("/api/sessions/{session_id}")
def get_session_by_id(session_id: str, scope: Optional[ScopeT] = Depends(optional_scope)):
    s = get_session(session_id)
    if s is None:
        raise HTTPException(404, detail="Session not found")
    return _serve_session(s, scope)


# ── Admin routes ───────────────────────────────────────────────────────────────

@app.delete("/api/runs")
async def clear_runs():
    """Admin: drop and recreate all tables. Test / dev use only."""
    clear_all()
    return {"cleared": True}


@app.get("/api/directive")
async def get_directive_info():
    d = get_active_directive()
    return {"version": d.version, "text": d.text}


@app.get("/api/self-report")
async def get_self_report():
    """
    What has actually been tested here, and what is actually broken.

    Deliberately unauthenticated and deliberately unflattering: this is the
    subsystem's own honest ledger, and the UI shows it prominently rather than
    burying it. Test counts are discovered live; live-model results and known
    issues are the recorded findings from logs/RUN_LOG.md and the docs under
    divij/, each carrying its own source reference (see web/self_report.py).
    """
    return build_self_report()
