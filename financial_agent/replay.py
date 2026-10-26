"""
Replay: answering tool calls from a stored bundle, and checking recorded calls against it.

Why this exists
    Tier-2 verification (PLAN.md 6.4) runs agent B on the data agent A saw. That only means
    something if (1) the stored bundle is the one A saw, and (2) every result A was given really
    came from that bundle. This module checks both before anything is replayed:

    - load_bundle() refuses a bundle whose data doesn't hash to its recorded hash.
    - check_recorded_calls() reruns every recorded call on the bundle and compares result
      hashes. A mismatch means the record and the data disagree (a bug, a changed indicator
      definition, or an edited record), and it is reported, never patched over.

    The replayed tools are financial_agent/tools.py's own functions, which never touch the
    network, so a replay can't fetch fresh data even by mistake. A call the bundle can't answer
    gets the same {"error": ...} it would have got the first time.
"""

from __future__ import annotations

from core.analysis import content_hash
from financial_agent.bundle import bundle_problems
from financial_agent.tools import ToolRecorder, run_tool


class ReplayError(ValueError):
    pass


def load_bundle(stored: dict) -> dict:
    """The stored bundle, if it is intact. Raises ReplayError naming every problem otherwise."""
    problems = bundle_problems(stored)
    if problems:
        raise ReplayError("; ".join(problems))
    return stored


def replay_recorder(stored: dict) -> ToolRecorder:
    """A fresh recorder over a verified bundle: what agent B calls."""
    return ToolRecorder(load_bundle(stored))


def check_recorded_calls(stored: dict, calls: list[dict]) -> list[dict]:
    """
    Every recorded call whose result doesn't match what the bundle gives today, as
    {"seq", "tool", "problem"}. [] means every result is reproducible from the data.
    """
    data = load_bundle(stored)["data"]
    out = []
    for i, call in enumerate(calls, 1):
        seq = call.get("seq")
        if seq != i:
            out.append({"seq": seq, "tool": call.get("tool"), "problem": f"expected seq {i}"})
        if content_hash(call.get("result")) != call.get("result_sha256"):
            out.append({"seq": seq, "tool": call.get("tool"),
                        "problem": "recorded result doesn't match its own hash"})
            continue
        again = run_tool(data, call.get("tool"), call.get("args") or {})
        if content_hash(again) != call.get("result_sha256"):
            out.append({"seq": seq, "tool": call.get("tool"),
                        "problem": "the bundle gives a different result for this call"})
    return out
