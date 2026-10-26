"""
Runs in progress, watchable by anyone who knows the run id.

A compare run started from the review app streams to the browser that started it
(web/server.py's _event_stream). A run started by an agent session over MCP
(web/mcp_server.py) has no browser attached, so the review app needs a way to watch a run it
didn't start: every compare run registers here while it is in progress, and
GET /api/runs/{id}/live replays the events so far and then follows new ones.

The events are the same ones the start-and-stream routes send (run_started, step_started,
step_finished, agent_finished, then result or error), so the review app's live view reads
them unchanged. A finished run stays here for FINISHED_TTL_S so a watcher that arrives a little
late still sees the whole trace; after that, the stored record is the place to read it.

Thread-safe: runs execute on worker threads, watchers read from the event loop.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

FINISHED_TTL_S = 15 * 60
TERMINAL = ("result", "error")


@dataclass
class LiveRun:
    run_id: str
    started_by: str                      # "review app", "api", or an agent session's label
    scope: str = "auditor"               # the scope the run is stored at; a token-less watcher reads at it
    summary: dict[str, Any] = field(default_factory=dict)
    events: list[tuple[str, dict]] = field(default_factory=list)
    finished_at: float | None = None
    _cond: threading.Condition = field(default_factory=threading.Condition, repr=False)

    def emit(self, event: str, data: dict) -> None:
        with self._cond:
            self.events.append((event, data))
            if event == "run_started":
                self.summary.update({k: data.get(k) for k in ("mode", "ticker", "subject")})
            if event in TERMINAL:
                self.finished_at = time.time()
            self._cond.notify_all()

    def wait_from(self, index: int, timeout: float) -> tuple[list[tuple[str, dict]], bool]:
        """Events from `index` on (waiting up to `timeout` if there are none yet), and whether
        the run has finished."""
        with self._cond:
            if len(self.events) <= index and self.finished_at is None:
                self._cond.wait(timeout)
            return self.events[index:], self.finished_at is not None


_RUNS: dict[str, LiveRun] = {}
_LOCK = threading.Lock()


def register(run_id: str, started_by: str, scope: str = "auditor") -> LiveRun:
    _prune()
    run = LiveRun(run_id, started_by, scope)
    with _LOCK:
        _RUNS[run_id] = run
    return run


def get(run_id: str) -> LiveRun | None:
    _prune()
    with _LOCK:
        return _RUNS.get(run_id)


def in_progress() -> list[dict[str, Any]]:
    _prune()
    with _LOCK:
        runs = list(_RUNS.values())
    return [{"run_id": r.run_id, "started_by": r.started_by, **r.summary} for r in runs if r.finished_at is None]


def _prune() -> None:
    cutoff = time.time() - FINISHED_TTL_S
    with _LOCK:
        for rid in [rid for rid, r in _RUNS.items() if r.finished_at and r.finished_at < cutoff]:
            del _RUNS[rid]
