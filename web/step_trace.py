"""
Step trace — what actually happened during a Cross-Agent Validation run, in order.

Why this exists
    The comparison result tells you what the comparator concluded. It does not tell
    you how either agent got there, and the UI needs that to be legible: which calls
    were made, in what order, which were shared between the two producers, and which
    belonged to only one.

    That last distinction is the point. /api/compare makes exactly ONE SEC EDGAR
    fetch and derives two contexts from it. A UI that renders a tool-call row under
    each agent would invent a second fetch. Phases exist so the shared work is
    recorded once as `shared` and can be drawn once.

Ordering is real, not cosmetic
    The two producers run concurrently by default inside run_cross_agent_validation
    (see its `concurrent` parameter), so their steps interleave. `seq` is the order
    steps *started*, assigned under a lock, and every step also carries a wall-clock
    `started_at` — together they let the UI draw two genuinely parallel lanes and
    still reconstruct exactly what overlapped with what. With
    CROSS_AGENT_MAX_CONCURRENCY=1 the producers run A-then-B again and `seq` reads
    as a plain sequence.

Live observation
    subscribe() registers a listener that receives ("step_started" | "step_finished",
    step_snapshot) as steps happen — this is what web/server.py's /stream routes
    forward to the browser. A listener sees copies, never the live dicts, so it
    can't mutate the record it is observing.

What it measures
    Wall-clock duration per step via time.monotonic(). Good enough to see that an
    LLM call took seconds and a summarize took microseconds; not a profiler, and
    not comparable across machines.

Scope
    Instrumentation lives in the route and in an adapter wrapper. pipeline/middleware.py and
    core/schemas.py know nothing about it. validation/cross_validation.py's only concession is an
    optional on_agent_finished callback (for live streams); it never imports this module.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from core.parsing import reject_unusable_conclusion

StepListener = Callable[[str, dict[str, Any]], None]

# Phases. `shared` is work done once and reused by both producers — the whole
# reason this enum exists rather than just tagging steps with an agent id.
PHASE_SHARED  = "shared"
PHASE_AGENT_A = "agent_a"
PHASE_AGENT_B = "agent_b"
PHASE_COMPARE = "compare"
PHASE_CHAT    = "chat"  # /api/chat's single agent — no producer split, so one phase covers it


class StepTrace:
    """
    Ordered record of the steps in one run, one per request.

    Thread-safe: both producers append into the same trace when they run
    concurrently. The lock only guards seq assignment and the list append — a
    step dict is only ever mutated afterwards by the one thread that owns it.
    """

    def __init__(self) -> None:
        self._steps: list[dict[str, Any]] = []
        self._seq = 0
        self._lock = threading.Lock()
        self._listeners: list[StepListener] = []

    def subscribe(self, listener: StepListener) -> None:
        self._listeners.append(listener)

    def _publish(self, event: str, step: dict[str, Any]) -> None:
        snapshot = dict(step)
        for listener in list(self._listeners):
            try:
                listener(event, snapshot)
            except Exception:
                # A broken observer (e.g. a browser that disconnected mid-stream)
                # must never break, or even alter, the run it is observing.
                pass

    def _new_step(self, phase: str, label: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            step: dict[str, Any] = {
                "seq": self._seq,
                "phase": phase,
                "label": label,
                "started_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "detail": None,
                "url": None,
                "status": "ok",
                "error": None,
                "duration_ms": None,
            }
            step.update(fields)
            self._steps.append(step)
        return step

    @contextmanager
    def record(
        self,
        phase: str,
        label: str,
        *,
        detail: str | None = None,
        url: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """
        Time a step. The yielded dict can be mutated by the caller to attach
        detail discovered while the step runs (a resolved CIK, a model name).

        extra carries structured fields for the UI (e.g. {"kind": "llm", "attempt": 2})
        so it never has to parse `label` strings to know what a step was.

        A raised exception is recorded as a failed step and re-raised — a step that
        blew up is more interesting than one that never appears, so the trace keeps
        it rather than swallowing it.
        """
        step = self._new_step(phase, label, detail=detail, url=url, **(extra or {}))
        self._publish("step_started", step)
        started = time.monotonic()
        try:
            yield step
        except BaseException as exc:
            # Don't clobber a more specific status the caller already set on its way
            # out (wrap_adapter marks a structural parse failure as `parse_failure`,
            # which is retryable and meaningfully different from a hard error).
            if step["status"] == "ok":
                step["status"] = "error"
            if step["error"] is None:
                step["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            step["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
            self._publish("step_finished", step)

    def note(
        self,
        phase: str,
        label: str,
        *,
        detail: str | None = None,
        url: str | None = None,
        status: str = "ok",
        duration_ms: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Record something instantaneous — a fact, not a timed operation.

        duration_ms is for a caller that already measured the duration itself
        (e.g. langchain_adapter.py's tool-call events, timed inside the async
        tool loop this class has no way to wrap with .record()) — leave it
        None for a genuinely instantaneous fact.
        """
        step = self._new_step(
            phase, label, detail=detail, url=url, status=status,
            duration_ms=duration_ms, **(extra or {}),
        )
        self._publish("step_finished", step)
        return step

    def to_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._steps)


def wrap_model_call(trace: "StepTrace", phase: str, call: Callable, *, model_label: str, prompt_version: str) -> Callable:
    """
    Time the assessment extraction (B4, option 1) as its own step, kind "extract",
    so the trace shows that a second model call read the answer — and whether it
    failed — instead of the grade appearing from nowhere.
    """
    def traced(system: str, user: str) -> str:
        with trace.record(phase, "Assessment extraction",
                          detail=f"model={model_label} · prompt={prompt_version}",
                          extra={"kind": "extract", "model": model_label}):
            return call(system, user)
    return traced


def wrap_adapter(
    trace: StepTrace,
    phase: str,
    adapter: Callable,
    *,
    model_label: str,
) -> Callable:
    """
    Wrap a producer's adapter so every LLM attempt becomes its own timed step.

    This is the only way to see per-attempt behaviour without touching
    pipeline/middleware.py: run_validation_loop calls the adapter once per attempt, and
    passes the DirectiveVersion it chose for that attempt. Attempt 2 arrives with
    the corrective directive (version "corrective"), so ADR-07's retry becomes
    visible in the trace if it ever fires — which, per logs/RUN_LOG.md, has never
    yet been observed against a real model.

    A StructuralParseError is recorded as a `parse_failure` step and re-raised
    unchanged, so pipeline/middleware.py's retry/halt logic behaves exactly as it would
    without the wrapper.
    """
    attempt = {"n": 0}

    def wrapped(subject: str, context: str, directive):
        attempt["n"] += 1
        n = attempt["n"]
        with trace.record(
            phase,
            f"LLM attempt {n}",
            detail=f"model={model_label} · directive={getattr(directive, 'version', '?')}",
            extra={"kind": "llm", "attempt": n, "model": model_label},
        ) as step:
            try:
                response = adapter(subject, context, directive)
                # Same check pipeline/middleware.py enforces; repeated here only so
                # this step reads parse_failure (with the echoed sentence as its
                # error) instead of ok when the conclusion restates the directive.
                reject_unusable_conclusion(response, getattr(directive, "text", ""))
            except Exception as exc:
                # Distinguish "the model broke the contract" (expected, retryable)
                # from any other adapter failure, so the UI can label them differently.
                step["status"] = (
                    "parse_failure"
                    if type(exc).__name__ == "StructuralParseError"
                    else "error"
                )
                step["error"] = f"{type(exc).__name__}: {exc}"
                raise
            step["status"] = "ok"
            return response

    return wrapped
