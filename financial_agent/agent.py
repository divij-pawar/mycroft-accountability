"""
The built-in financial agent: a request in, a stored-ready analysis record out.

What it does
    1. plan_request(): finds the ticker and horizon in a free-text message (or says why it can't).
    2. prepare_bundle(): freezes the data (prices, SEC filing facts) into a bundle.
    3. run_analysis(): lets a model call the data tools over that bundle, asks for the three-block
       reply (prompts.py), and runs it through the same retry-then-halt loop every other agent in
       the layer goes through (pipeline/middleware.py, ADR-07). Every attempt is kept, every tool
       call is recorded with its result's hash, and the result is an AnalysisRecord
       (core/analysis.py) ready for web/db.py to append.

What it does not do
    It never checks that the signal is right, or that its figures match the tools' results; that
    is the verification layer's job (validation/, later phases), and this module doesn't import it
    (PLAN.md D1). A signal is a model's reading of the data it was given (P8), not advice.

The model is injected as a ModelStep, a function from the conversation so far to the model's
next turn, so tests drive the whole path with scripted turns and no model:

    ModelStep = (messages) -> {"content": str, "tool_calls": [{"id", "name", "args"}, ...]}
    messages: {"role": "system" | "user" | "assistant" | "tool", "content": str,
               "tool_calls"?: [...], "tool_call_id"?: str}

langchain_model_step() is the real one: LangChain over Ollama or Gemini, the data tools bound in
this process. `inputs_captured` is True for this agent because every figure it can use came through
the recorded tools; it says nothing about what the model remembers from training.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from core.analysis import (
    DEFAULT_HORIZON,
    RECORD_SCHEMA,
    ParsedSignal,
    validate_signal,
)
from core.directive import DirectiveVersion
from core.parsing import (
    AgentResponse,
    StructuralParseError,
    _CONCLUSION_RE,
    _THOUGHT_LOG_RE,
    reject_unusable_conclusion,
)
from core.schemas import AgentID
from datasources.tickers import ticker_for
from financial_agent import prompts
from financial_agent.bundle import build_bundle, fetch_filing_snapshot
from financial_agent.prices import fetch_prices
from financial_agent.tools import TOOL_SPECS, ToolRecorder
from pipeline.middleware import HaltError, run_validation_loop

ModelStep = Callable[[list[dict]], dict]
Emit = Callable[[str, dict], None]

MAX_TOOL_ROUNDS = 8         # model turns per attempt; a model that never stops asking for data is cut off
MAX_MESSAGE_CHARS = 2000

# Models that think natively. A name not listed here is "none": its thought log is shown as
# working notes, never as thinking (PLAN.md D4). Extend this from measurement, not from a model card.
_NATIVE_REASONING = ("deepseek-r1", "qwq", "qwen3", "gpt-oss", "gemini-2.5")


def reasoning_mode(model: str) -> str:
    name = (model or "").lower()
    return "native" if any(n in name for n in _NATIVE_REASONING) else "none"


# ── The request ────────────────────────────────────────────────────────────────

class RequestError(ValueError):
    """The message can't be turned into an analysis; the text says what to change."""


_DURATION_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*[- ]?\s*(day|week|month|year|yr|quarter)s?\b", re.I)
_MONTHS_PER = {"day": 1 / 30, "week": 7 / 30, "month": 1, "quarter": 3, "year": 12, "yr": 12}


def _horizon_for_months(months: float) -> str:
    if months <= 3:
        return "1-3m"
    if months <= 6:
        return "3-6m"
    if months <= 12:
        return "6-12m"
    return "12m+"


def extract_horizon(message: str) -> tuple[str, bool]:
    """(horizon, defaulted): the horizon the message names, else the default and True."""
    matches = list(_DURATION_RE.finditer(message))
    if matches:
        m = matches[-1]   # "3 to 6 months" names 6 months: the end of the range
        return _horizon_for_months(float(m.group(1)) * _MONTHS_PER[m.group(2).lower()]), False
    text = message.lower()
    if re.search(r"\blong[- ]term\b|\bmulti[- ]year\b", text):
        return "12m+", False
    if re.search(r"\bshort[- ]term\b|\bnext (few )?weeks?\b|\bthis (quarter|month)\b", text):
        return "1-3m", False
    if re.search(r"\bnext (year|12 months)\b|\bthis year\b", text):
        return "6-12m", False
    return DEFAULT_HORIZON, True


def plan_request(message: str) -> dict:
    """
    {"message", "ticker", "horizon", "horizon_defaulted"} for a free-text request, or
    RequestError. The ticker comes from datasources/tickers.py (a symbol, or a company name
    written with a capital letter); nothing is guessed beyond that.
    """
    text = (message or "").strip()
    if not text:
        raise RequestError("Write a request that names a company, for example: Should I buy AAPL?")
    if len(text) > MAX_MESSAGE_CHARS:
        raise RequestError(f"Keep the request under {MAX_MESSAGE_CHARS} characters.")
    ticker = ticker_for(text)
    if ticker is None:
        raise RequestError("I couldn't find a listed company in that. Name one by its ticker (AAPL) "
                           "or its name with a capital letter (Apple).")
    horizon, defaulted = extract_horizon(text)
    return {"message": text, "ticker": ticker, "horizon": horizon, "horizon_defaulted": defaulted}


# ── The data ───────────────────────────────────────────────────────────────────

def prepare_bundle(
    ticker: str,
    *,
    prices_fn: Callable[[str], dict] = fetch_prices,
    filing_fn: Callable[[str], dict] = fetch_filing_snapshot,
) -> tuple[dict, list[str]]:
    """
    (bundle, notes). Prices are required and a PriceFetchError propagates, saying why. SEC filing
    facts are not: if EDGAR is unreachable or doesn't list the ticker the bundle has none, the
    agent is told so by describe_data, and the note says why.
    """
    prices = prices_fn(ticker)
    notes: list[str] = []
    try:
        filing = filing_fn(ticker)
    except Exception as exc:
        filing = None
        notes.append(f"SEC filing facts unavailable: {type(exc).__name__}: {exc}")
    return build_bundle(ticker=ticker, prices=prices, filing=filing), notes


# ── Reading the reply ──────────────────────────────────────────────────────────

_SIGNAL_RE = re.compile(r"\s*<signal>(.*?)</signal>", re.DOTALL)


def parse_analysis_response(raw: str) -> tuple[AgentResponse, ParsedSignal]:
    """
    The three-block reply as (the thought log and conclusion, the checked signal), or
    StructuralParseError saying which rule it broke. The JSON must parse as written: a
    fenced or commented block fails, so the format pass rate measures what the model did.
    """
    thought = _THOUGHT_LOG_RE.search(raw)
    conclusion = _CONCLUSION_RE.search(raw, thought.end() if thought else 0)
    if not thought and not conclusion:
        raise StructuralParseError("the reply has none of the <thought_log>, <conclusion> and <signal> blocks", raw)
    if not thought:
        raise StructuralParseError("the reply has no <thought_log> block", raw)
    if not conclusion:
        raise StructuralParseError("the reply has no <conclusion> block", raw)
    signal = _SIGNAL_RE.match(raw, conclusion.end())
    if not signal:
        raise StructuralParseError("the reply has no <signal> block directly after <conclusion>", raw)

    spans = sorted([thought.span(), conclusion.span(), signal.span()])
    remainder, at = "", 0
    for start, end in spans:
        remainder += raw[at:start]
        at = end
    remainder += raw[at:]
    if remainder.strip():
        raise StructuralParseError("the reply has text outside the three blocks", raw)

    try:
        obj = json.loads(signal.group(1))
    except json.JSONDecodeError as exc:
        raise StructuralParseError(f"the <signal> block is not valid JSON ({exc.msg})", raw) from None
    parsed = validate_signal(obj)
    if parsed.status == "invalid":
        raise StructuralParseError("the <signal> block is invalid: " + "; ".join(parsed.issues), raw)

    return AgentResponse(thought_log=thought.group(1).strip(), conclusion=conclusion.group(1).strip(),
                         raw_text=raw), parsed


# ── The tool loop ──────────────────────────────────────────────────────────────

class Steps:
    """The step trace of one analysis: numbered, kept in order, and sent to `emit` as it grows."""

    def __init__(self, emit: Emit | None):
        self.items: list[dict] = []
        self._emit = emit

    def add(self, kind: str, label: str, status: str = "ok", detail: str | None = None,
            duration_ms: float | None = None, **extra: Any) -> dict:
        step = {"seq": len(self.items) + 1, "kind": kind, "label": label, "status": status,
                "detail": detail, "duration_ms": duration_ms, **extra}
        self.items.append(step)
        if self._emit is not None:
            self._emit("step", step)
        return step


def _tool_message(entry: dict) -> str:
    return json.dumps({"call": entry["seq"], "tool": entry["tool"], "result": entry["result"]},
                      separators=(",", ":"), ensure_ascii=False)


def _answer(step: ModelStep, messages: list[dict], recorder: ToolRecorder, steps: Steps,
            max_rounds: int) -> str:
    """Let the model call tools until it writes a reply without any; return that reply's text."""
    for _ in range(max_rounds):
        reply = step(messages)
        calls = reply.get("tool_calls") or []
        content = reply.get("content") or ""
        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        if not calls:
            return content
        for call in calls:
            started = time.monotonic()
            recorder.call("a", call["name"], call.get("args"))
            entry = recorder.calls[-1]
            failed = "error" in entry["result"]
            steps.add("tool", entry["tool"], "error" if failed else "ok",
                      detail=str(entry["result"]["error"]) if failed else None,
                      duration_ms=round((time.monotonic() - started) * 1000, 1),
                      call=entry["seq"], args=entry["args"])
            messages.append({"role": "tool", "tool_call_id": call.get("id") or f"call_{entry['seq']}",
                             "content": _tool_message(entry)})
    raise StructuralParseError(
        f"the model was still asking for data after {max_rounds} turns and never wrote its reply",
        messages[-1].get("content") or "")


def make_analysis_adapter(step: ModelStep, recorder: ToolRecorder, steps: Steps, request: dict,
                          bundle: dict, state: dict, max_rounds: int = MAX_TOOL_ROUNDS):
    """
    The AgentAdapter pipeline/middleware.py calls: (subject, context, directive) -> AgentResponse,
    raising StructuralParseError for a reply that breaks the contract. `state` receives the
    checked signal of the attempt that passed. The second attempt arrives with the loop's generic
    corrective directive; this adapter swaps in text that names what was actually wrong.
    """
    state.update({"attempt": 0, "signal": None, "last_error": None, "corrective_text": None})

    def adapter(subject: str, context: str, directive: DirectiveVersion) -> AgentResponse:
        state["attempt"] += 1
        attempt = state["attempt"]
        user = prompts.user_prompt(request, bundle)
        if directive.version == "corrective":
            state["corrective_text"] = prompts.corrective_text(state["last_error"])
            user += "\n\n" + state["corrective_text"]
        messages = [{"role": "system", "content": prompts.ANALYSIS_DIRECTIVE},
                    {"role": "user", "content": user}]
        started = time.monotonic()
        try:
            raw = _answer(step, messages, recorder, steps, max_rounds)
            response, parsed = parse_analysis_response(raw)
            # The loop checks an echo against the directive of that attempt, and the retry's is the
            # generic corrective text, so a reply that repeats this agent's instructions would pass
            # the second attempt unchecked. Check it against the real one here.
            reject_unusable_conclusion(response, prompts.ANALYSIS_DIRECTIVE)
        except StructuralParseError as exc:
            state["last_error"] = str(exc)
            steps.add("llm", f"Attempt {attempt}", "parse_failure", detail=str(exc),
                      duration_ms=round((time.monotonic() - started) * 1000, 1), attempt=attempt)
            raise
        state["signal"] = parsed
        steps.add("llm", f"Attempt {attempt}", "ok",
                  duration_ms=round((time.monotonic() - started) * 1000, 1), attempt=attempt)
        return response

    return adapter


# ── One analysis ───────────────────────────────────────────────────────────────

def _attempts(objects) -> list[dict]:
    return [{"attempt": ro.attempt_number, "parse_status": ro.parse_status.value,
             "raw_text": (ro.raw_output or {}).get("text"), "directive_version": ro.directive_version}
            for ro in objects]


def run_analysis(
    request: dict,
    *,
    bundle: dict,
    model_step: ModelStep,
    model: str,
    temperature: float = 0.0,
    seed: int | None = None,
    emit: Emit | None = None,
    run_id: uuid.UUID | None = None,
    parent_run_id: str | None = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
    steps: Steps | None = None,
) -> dict:
    """
    One analysis as an AnalysisRecord (core/analysis.py): status "ok" with the signal, or
    "halted" with every attempt kept and no signal. A model that can't be reached raises
    (nothing is recorded), as in the compare routes.
    """
    run_id = run_id or uuid.uuid4()
    steps = steps or Steps(emit)
    recorder = ToolRecorder(bundle)
    state: dict = {}
    adapter = make_analysis_adapter(model_step, recorder, steps, request, bundle, state, max_rounds)
    directive = DirectiveVersion(version=prompts.ANALYSIS_PROMPT_VERSION, text=prompts.ANALYSIS_DIRECTIVE)
    context = f"bundle {bundle['bundle_hash']} as of {bundle['data']['as_of']}"

    output, response, halt_reason = None, None, None
    try:
        result = run_validation_loop(request["message"], context, run_id, AgentID.EXTERNAL,
                                     directive=directive, call_agent_fn=adapter)
        objects, response = result.reasoning_objects, result.final_response
    except HaltError as exc:
        objects, halt_reason = exc.reasoning_objects, str(exc)

    issues: list[str] = []
    if response is not None:
        parsed: ParsedSignal = state["signal"]
        output = dict(parsed.signal)
        issues = list(parsed.issues)
        if "horizon" not in output:
            output["horizon"] = request["horizon"]
            issues.append(f"the signal named no valid horizon, so the request's ({request['horizon']}) is shown")
    steps.add("result", "Signal recorded" if output else "Halted", "ok" if output else "halt",
              detail=None if output else halt_reason)

    return {
        "schema": RECORD_SCHEMA,
        "run_id": str(run_id),
        "parent_run_id": parent_run_id,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "request": {k: request[k] for k in ("message", "ticker", "horizon", "horizon_defaulted")},
        "agent": {"kind": "builtin", "model": model, "reasoning": reasoning_mode(model),
                  "prompt_version": prompts.ANALYSIS_PROMPT_VERSION, "temperature": temperature,
                  "seed": seed, "label": None},
        "bundle_hash": bundle["bundle_hash"],
        "data_as_of": bundle["data"]["as_of"],
        "inputs_captured": True,
        "tool_calls": recorder.calls,
        "steps": steps.items,
        "attempts": _attempts(objects),
        "corrective_text": state.get("corrective_text"),
        "output": output,
        "conclusion": response.conclusion if response else None,
        "thought_log": response.thought_log if response else None,
        "issues": issues,
        "halt_reason": halt_reason,
        "status": "ok" if output else "halted",
    }


# ── The real model ─────────────────────────────────────────────────────────────

def tool_schemas() -> list[dict]:
    """The data tools as function-calling schemas, from the same TOOL_SPECS the replay uses."""
    def spec(name: str, properties: dict, required: list[str] | None = None) -> dict:
        notes = "; ".join(f"{k}: {v}" for k, v in TOOL_SPECS[name]["params"].items())
        description = TOOL_SPECS[name]["description"] + (f" Arguments: {notes}." if notes else "")
        parameters: dict = {"type": "object", "properties": properties}
        if required:
            parameters["required"] = required
        return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}

    return [
        spec("describe_data", {}),
        spec("get_prices", {"series": {"type": "string", "enum": ["daily", "weekly"]},
                            "last": {"type": "integer"}}),
        spec("get_indicators", {"requests": {"type": "array", "items": {
            "type": "object", "properties": {"name": {"type": "string"}, "params": {"type": "object"}},
            "required": ["name"]}}}),
        spec("get_filing_facts", {"concepts": {"type": "array", "items": {"type": "string"}}}),
    ]


def langchain_model_step(model: str, temperature: float = 0.0, seed: int | None = None,
                         max_tokens: int | None = None) -> ModelStep:
    """
    The real model: LangChain over Ollama (or Gemini for a Gemini-family name), the data tools
    bound. A connection failure or a stall past MODEL_TIMEOUT_S raises LangchainConnectionError,
    as every other call in the layer does.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    from adapters.langchain_adapter import _build_chat, _connection_error, _sanitize_tool_args

    bound = _build_chat(model, temperature, seed, max_tokens).bind_tools(tool_schemas())

    def to_langchain(messages: list[dict]) -> list:
        out = []
        for m in messages:
            role = m["role"]
            if role == "system":
                out.append(SystemMessage(content=m["content"]))
            elif role == "user":
                out.append(HumanMessage(content=m["content"]))
            elif role == "assistant":
                out.append(AIMessage(content=m["content"], tool_calls=[
                    {"id": c["id"], "name": c["name"], "args": c.get("args") or {}} for c in m.get("tool_calls") or []]))
            else:
                out.append(ToolMessage(content=m["content"], tool_call_id=m["tool_call_id"]))
        return out

    def step(messages: list[dict]) -> dict:
        try:
            reply = bound.invoke(to_langchain(messages))
        except EnvironmentError:
            raise
        except Exception as exc:
            raise _connection_error(model, exc, "") from exc
        content = reply.content if isinstance(reply.content, str) else str(reply.content)
        calls = [{"id": c.get("id") or f"call_{i}", "name": c["name"],
                  "args": _sanitize_tool_args(c.get("args") or {})}
                 for i, c in enumerate(reply.tool_calls or [], 1)]
        return {"content": content, "tool_calls": calls}

    return step
