"""
Provider registry — LangChain is the one agent framework this subsystem is
built on (see adapters/langchain_adapter.py). This module still exists as a
single, one-entry seam rather than being inlined into web/server.py: the
same `build_adapter` / `model_label` / `with_model_override` calls that used
to route between three provider adapters now route to the one, so a future
second framework (if one is ever added) still costs one ProviderSpec entry,
not a rewrite of every caller.

gemini_adapter.py and ollama_adapter.py (and the "mock" provider before them)
are archived at archive/adapters/ — not deleted, not callable — now that
langchain_adapter.py drives both Ollama- and Gemini-family models itself via
LangChain's own interface (model-name inference, not a provider choice).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from adapters.langchain_adapter import make_langchain_adapter, make_langchain_model_call
from core.contracts import AgentAdapter


@dataclass(frozen=True)
class ProviderSpec:
    """
    Everything the rest of the system needs to know about one provider.

    `model_key` is the config key that provider reads its model name from.

    `supports_tools` is what web/server.py checks before silently letting a
    citation go unverifiable. With only one framework, this is no longer
    "does this provider have a tool-calling capability" (always true) — it's
    repurposed to mean "is that capability actually usable right now": for
    `langchain`, whether `TAVILY_API_KEY` is set. Declared as a callable
    rather than a fixed bool so it can depend on live environment state, not
    just which provider was picked.
    """

    name: str
    model_key: str | None
    default_model: str | None
    build: Callable[[Mapping], AgentAdapter]
    describe: Callable[[Mapping], str]
    supports_tools: Callable[[], bool] = lambda: False


def _tavily_configured() -> bool:
    import os
    return bool(os.environ.get("TAVILY_API_KEY"))


def _build_langchain(cfg: Mapping) -> AgentAdapter:
    # "_on_tool_event" is a private, transient key a caller can inject into a
    # per-call config copy (never into the shared _config dict itself, which
    # gets json.dumps'd into config_snapshot — a callable would break that) to
    # receive every tool call this adapter makes. See web/server.py's
    # /api/chat route and langchain_adapter.py's own docstring.
    return make_langchain_adapter(
        model=cfg.get("model") or "llama3.2",
        temperature=cfg.get("temperature", 0.0),
        seed=cfg.get("seed", 42),
        on_tool_event=cfg.get("_on_tool_event"),
    )


PROVIDERS: dict[str, ProviderSpec] = {
    "langchain": ProviderSpec(
        name="langchain",
        model_key="model",
        default_model="llama3.2",
        build=_build_langchain,
        describe=lambda cfg: f"langchain:{cfg.get('model') or 'llama3.2'}",
        supports_tools=_tavily_configured,
    ),
}


def provider_names() -> tuple[str, ...]:
    """Valid provider names — one, today, but callers still ask rather than assume."""
    return tuple(PROVIDERS)


def resolve(provider: str) -> ProviderSpec:
    """Look up a provider, failing loudly with the full valid set in the message."""
    try:
        return PROVIDERS[provider]
    except KeyError:
        raise ValueError(
            f"Unknown provider {provider!r}; expected one of {', '.join(PROVIDERS)}"
        ) from None


def build_adapter(config: Mapping) -> AgentAdapter:
    """Build the adapter this config selects."""
    return resolve(config["provider"]).build(config)


def build_model_call(config: Mapping):
    """A plain model call on the same model as this config's agent (the assessment extraction)."""
    resolve(config["provider"])  # one provider today; still fail loudly on an unknown one
    return make_langchain_model_call(
        model=config.get("model") or "llama3.2",
        temperature=config.get("temperature", 0.0),
        seed=config.get("seed", 42),
    )


def model_label(config: Mapping) -> str:
    """Human-readable `provider:model` for the step trace, the UI and run audit trails."""
    return resolve(config["provider"]).describe(config)


def with_model_override(config: Mapping, model: str | None = None) -> dict:
    """
    Shallow-copy `config` with one producer's model override applied.

    No provider argument anymore — there is only one provider, so an override
    is purely a model name (which may be an Ollama-family or a Gemini-family
    name; langchain_adapter.py infers which per-call).
    """
    cfg = dict(config)
    if model:
        key = resolve(cfg["provider"]).model_key
        if key:
            cfg[key] = model
    return cfg
