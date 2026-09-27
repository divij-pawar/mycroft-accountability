"""
The abstractions every other layer depends on (DIP).

Until this module existed, the two central contracts of this subsystem were
documented only in prose: the agent-adapter signature lived in a two-line comment
at the top of `adapters/__init__.py`, and the injectable HTTP fetcher lived in
repeated `Callable[[str], dict]` annotations. Both were real contracts with no
name, so nothing could be checked against them and nothing could refer to them.

Naming them here means the dependency arrows point inward: adapters *implement*
`AgentAdapter`, `pipeline.middleware` *consumes* one, and neither has to know the
other exists. These are `typing.Protocol`s, so conformance is structural — an
adapter satisfies `AgentAdapter` by having the right shape, with no base class to
inherit and no runtime cost.

ADR-01b/ADR-07: the directive argument is what makes the retry in
`pipeline.middleware.run_validation_loop` possible — attempt 2 passes a different
`DirectiveVersion` to the same adapter, so the directive must be a parameter of
the contract rather than something an adapter captures at construction time.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.directive import DirectiveVersion
from core.parsing import AgentResponse


@runtime_checkable
class AgentAdapter(Protocol):
    """
    What every adapter in `adapters/` returns and what the validation loop calls.

    Implementations must either return a structurally valid `AgentResponse` or
    raise `core.parsing.StructuralParseError`. Raising is not a failure of the
    adapter — it is the signal ADR-07's retry-then-halt loop is built on, so an
    adapter must not swallow it and must not invent a well-formed response to
    hide it.
    """

    def __call__(
        self, subject: str, context: str, directive: DirectiveVersion
    ) -> AgentResponse: ...


@runtime_checkable
class ModelCall(Protocol):
    """
    One plain model call: (system prompt, user prompt) -> the model's raw reply.

    Used for the assessment extraction (core/assessment.py, option 1 of B4): a
    second, separate call that reads an agent's finished answer and returns its
    grade/direction as JSON. Kept apart from AgentAdapter on purpose — it has no
    directive, no tool use and no structural contract, so it can't disturb ADR-07.
    Raises on a connection failure; never invents a reply.
    """

    def __call__(self, system: str, user: str) -> str: ...


@runtime_checkable
class JsonFetcher(Protocol):
    """
    A one-argument URL -> parsed-JSON callable.

    Every network read in this subsystem is injected as one of these, which is
    why the automated suite can be network-free without patching module globals:
    a test passes its own fixture fetcher instead. P2 (only ingest code touches
    the network) is enforced by the fact that the only default implementation
    lives in `datasources.edgar`.
    """

    def __call__(self, url: str) -> dict: ...
