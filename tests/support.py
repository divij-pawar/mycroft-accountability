"""
Test-only scripted adapter — dependency injection against the `AgentAdapter`
contract, used solely to test pipeline/middleware.py's ADR-07 retry/halt
control flow deterministically.

Why this exists, and why it is not a "mock feature" of the product:
  This repo's own live-model testing (see web/self_report.py's
  `retry-halt-unproven` entry) found that real models essentially never fail
  to comply with the response format — 24 real agent-runs produced zero
  retries and zero halts. That means ADR-07's retry-then-halt logic, one of
  this subsystem's core P4 guarantees, cannot be deterministically exercised
  by calling a real model: there is no reliable way to make a real model
  produce a structural parse failure on demand.

  Substituting a controlled fake callable for the `AgentAdapter` the
  validation loop calls is standard dependency-injection testing of our own
  Python control flow — the same category as `unittest.mock.MagicMock`, not
  a simulated LLM "answer" served to an end user. It is never registered in
  `adapters/registry.py`, never selectable as a runtime provider, and the
  running application cannot reach it through any code path.
"""

from __future__ import annotations

from core.contracts import AgentAdapter
from core.parsing import AgentResponse, StructuralParseError, _parse_response
from core.directive import DirectiveVersion

FAILURE_MODES: tuple[str, ...] = ("none", "retry_success", "halt")

_VALID_TEMPLATE = (
    "<thought_log>\n"
    "  Subject: {subject}\n"
    "  Context: {context}\n"
    "  {note}\n"
    "  Reasoning: evaluated all available inputs and reached a conclusion.\n"
    "</thought_log>\n"
    "<conclusion>\n"
    "  Test response for: {subject}\n"
    "  {note}\n"
    "</conclusion>"
)

_INVALID_RESPONSE = (
    "Sure! Here is my analysis of your request. "
    "I considered several factors and think the answer is probably yes, "
    "though it depends on the context. Let me know if you need more detail."
)


def make_scripted_adapter(failure_mode: str = "none") -> AgentAdapter:
    """
    failure_mode:
        "none"          — succeeds on attempt 1
        "retry_success" — fails on attempt 1 (PARSE_FAILURE), succeeds on attempt 2
        "halt"          — fails on both attempts (HALT)
    """
    if failure_mode not in FAILURE_MODES:
        raise ValueError(f"Unknown failure_mode: {failure_mode!r}")

    call_count = {"n": 0}

    def adapter(subject: str, context: str, directive: DirectiveVersion) -> AgentResponse:
        call_count["n"] += 1
        n = call_count["n"]

        if failure_mode == "none":
            return _parse_response(_VALID_TEMPLATE.format(
                subject=subject,
                context=context or "none provided",
                note="No failures simulated.",
            ))

        if failure_mode == "retry_success":
            if n == 1:
                raise StructuralParseError(
                    "Simulated structural failure on attempt 1",
                    _INVALID_RESPONSE,
                )
            return _parse_response(_VALID_TEMPLATE.format(
                subject=subject,
                context=context or "none provided",
                note="Recovered on retry with corrective directive.",
            ))

        if failure_mode == "halt":
            raise StructuralParseError(
                f"Simulated structural failure on attempt {n}",
                _INVALID_RESPONSE + f" (attempt {n})",
            )

    return adapter


def no_model_extraction():
    """
    Patch /api/compare's assessment-extraction builder (web.server._build_model_call,
    B4 option 1) with one that is unavailable, so a route test never reaches a real
    model. The route records that as extraction_failed — the degraded path it must
    handle anyway. Every test that runs a ticker compare applies this; a test that
    wants a real reply patches _build_model_call with a scripted one instead.

    (Not a package-wide guard in tests/__init__.py: web/self_report.py imports the
    test package at server startup to count tests, so a global patch there switched
    real model calls off in the running server — found live on 2026-09-26.)
    """
    from unittest.mock import patch

    def build(cfg):
        def call(system: str, user: str) -> str:
            raise ConnectionError("no model in tests")
        return call

    return patch("web.server._build_model_call", side_effect=build)
