"""
An agent session (Claude Code, Codex, Cursor or any MCP client) standing in for a model.

The verification layer normally calls a model itself, through an AgentAdapter
(core/contracts.py). Over MCP (web/mcp_server.py) the model is in someone else's session: an
agent the user runs with their own login. `SessionAgent` satisfies the same contracts, so the
whole pipeline (the ADR-07 retry-then-halt loop, the comparison, the checks, the gate) runs
unchanged. When the loop calls it, it hands the prompt to the session and waits for the
session's answer:

    validation loop ──calls──▶ SessionAgent.adapter ──prompt──▶ session (via the MCP `submit` reply)
                    ◀─AgentResponse── parse ◀──────────text──── session calls `submit`

What the session submits is parsed by the server (core/parsing.py), not by the agent, so a
reply that breaks the output contract is a structural failure exactly as a model's would be,
and the retry is the loop's own second call. Only what passes through the server is recorded.

The session's identity (its label) is what the MCP client reports about itself: recorded as
given, never verified, like the reviewer's name at the gate.

No network and no model here; web/mcp_server.py and web/server.py wire it in.
"""

from __future__ import annotations

import itertools
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from adapters.langchain_adapter import LangchainConnectionError
from core.assessment import expects_assessment
from core.parsing import AgentResponse, StructuralParseError, _parse_response

# How long the layer waits for a session to answer one prompt before the run is halted.
# A person may be reading along, so this is far longer than MODEL_TIMEOUT_S.
SESSION_TIMEOUT_S = float(os.environ.get("AGENT_SESSION_TIMEOUT_S", "900"))

ToolEvent = Callable[[dict[str, Any]], None]


class SessionTimeout(LangchainConnectionError):
    """The agent session sent nothing for SESSION_TIMEOUT_S seconds, so the run was halted."""


@dataclass
class SessionAgent:
    run_id: str
    slot: str
    label: str
    timeout_s: float = field(default_factory=lambda: SESSION_TIMEOUT_S)  # read when created, so it can be set
    on_tool_event: ToolEvent | None = None
    attempts: int = 0
    finished: bool = False
    joined: bool = False                     # a second session has claimed this slot (join_compare)
    _to_session: "queue.Queue[dict]" = field(default_factory=queue.Queue, repr=False)
    _from_session: "queue.Queue[str]" = field(default_factory=queue.Queue, repr=False)
    _last_error: str | None = field(default=None, repr=False)
    _tool_seq: Any = field(default_factory=lambda: itertools.count(1), repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── the contracts the pipeline calls ──

    def adapter(self, subject: str, context: str, directive) -> AgentResponse:
        """core.contracts.AgentAdapter: one attempt of this slot's answer."""
        with self._lock:
            self.attempts += 1
            attempt = self.attempts
        self._to_session.put({
            "kind": "answer",
            "attempt": attempt,
            "directive_version": getattr(directive, "version", None),
            "instructions": getattr(directive, "text", ""),
            "subject": subject,
            "context": context,
            "previous_attempt_failed": (self._last_error or "the previous reply failed the output contract")
                                       if attempt > 1 else None,
        })
        text = self._wait()
        try:
            return _parse_response(text, allow_assessment=expects_assessment(getattr(directive, "version", None)))
        except StructuralParseError as exc:
            self._last_error = str(exc)
            raise

    def model_call(self, system: str, user: str) -> str:
        """core.contracts.ModelCall: the assessment extraction, asked of the same session."""
        self._to_session.put({"kind": "assess", "instructions": system, "input": user})
        return self._wait()

    def finish(self, halted: bool) -> None:
        """Called when this slot's loop is over (accepted or halted)."""
        self.finished = True
        self._to_session.put({"kind": "slot_done", "halted": halted})

    def run_over(self, error: str | None) -> None:
        """Called when the whole run is over, so a session still waiting is released."""
        self._to_session.put({"kind": "run_done", "error": error})

    # ── the session side (web/mcp_server.py) ──

    def submit(self, text: str) -> None:
        self._from_session.put(text)

    def next_message(self, timeout: float) -> dict | None:
        try:
            return self._to_session.get(timeout=timeout)
        except queue.Empty:
            return None

    def record_search(self, query: str, run: Callable[[str], Any], urls: Callable[[Any], list],
                      snippets: Callable[[Any], list]) -> Any:
        """Run a search on the session's behalf and record it in the trace, as the server
        observed it (the same event shape the LangChain adapter's tool loop reports)."""
        seq = next(self._tool_seq)
        base = {"seq": seq, "tool": "layer_search", "url": None, "query": query, "args": {"query": query}}
        if self.on_tool_event:
            self.on_tool_event({**base, "phase": "started", "status": "ok", "result_preview": None, "duration_ms": None})
        started = time.monotonic()
        status, result = "ok", None
        try:
            result = run(query)
        except Exception as exc:  # a failed search is reported, never faked
            status, result = "error", f"Tool error: {type(exc).__name__}: {exc}"
        if self.on_tool_event:
            self.on_tool_event({**base, "phase": "finished", "status": status,
                                "result_preview": str(result)[:300], "urls": urls(result) if status == "ok" else [],
                                "results": snippets(result) if status == "ok" else [],
                                "duration_ms": round((time.monotonic() - started) * 1000, 1)})
        return result

    def _wait(self) -> str:
        try:
            return self._from_session.get(timeout=self.timeout_s)
        except queue.Empty:
            raise SessionTimeout(
                f"The agent session {self.label!r} sent nothing for {self.timeout_s:g} s, so the run was halted "
                f"(AGENT_SESSION_TIMEOUT_S).") from None


# ── Session runs ──────────────────────────────────────────────────────────────────


@dataclass
class SessionRun:
    run_id: str
    agents: dict[str, SessionAgent]          # the slots driven by a session
    partner: str                             # "ollama" or "session"
    thread: threading.Thread | None = None
    error: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


_RUNS: dict[str, SessionRun] = {}
_LOCK = threading.Lock()


def start(run_id: str, agents: dict[str, SessionAgent], partner: str, work: Callable[[], dict]) -> SessionRun:
    """Run `work` (the compare) on a background thread; release every session when it ends."""
    run = SessionRun(run_id, agents, partner)

    def body() -> None:
        try:
            payload = work()
            run.error = payload.get("error") if isinstance(payload, dict) else None
        except Exception as exc:  # never leave a session waiting on a dead run
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            run.done.set()
            for agent in run.agents.values():
                agent.run_over(run.error)

    run.thread = threading.Thread(target=body, name=f"session-run-{run_id[:8]}", daemon=True)
    with _LOCK:
        _RUNS[run_id] = run
    run.thread.start()
    return run


def get(run_id: str) -> SessionRun | None:
    with _LOCK:
        return _RUNS.get(run_id)
