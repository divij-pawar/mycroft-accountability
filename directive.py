"""
Accountability Layer — System Directive
Versioned, hardcoded source artifact. Not configurable at runtime.
Directive changes require a code deployment and produce a new directive version.

ADR-01b: Active middleware — agents are behaviourally modified by this directive.
ADR-05:  Stored verbatim per run — not referenced by pointer.
SEC-04:  No runtime parameter can modify directive content.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DirectiveVersion:
    version: str
    text: str


# ─────────────────────────────────────────────
# Registry of all directive versions.
# Old versions are kept so RunSessions from past deployments
# remain auditable against the exact directive that was active.
# ─────────────────────────────────────────────

_DIRECTIVE_REGISTRY: dict[str, DirectiveVersion] = {}


def _register(version: str, text: str) -> DirectiveVersion:
    d = DirectiveVersion(version=version, text=text)
    _DIRECTIVE_REGISTRY[version] = d
    return d


# ─────────────────────────────────────────────
# v1.0.0 — initial prototype directive
# ─────────────────────────────────────────────

DIRECTIVE_V1_0_0 = _register(
    version="v1.0.0",
    text=(
        "You are a financial analysis agent operating inside the Mycroft pipeline. "
        "You MUST structure your entire response in exactly two XML blocks and nothing else.\n\n"
        "First block — your internal reasoning:\n"
        "<thought_log>\n"
        "  Write every step of your reasoning here. Include: which data sources you consulted "
        "and why, any data quality issues you observed (missing data, stale filings, simulated "
        "values), intermediate conclusions you drew and then revised, and any uncertainty you "
        "encountered. Be explicit about confidence — if you are uncertain, say so here.\n"
        "</thought_log>\n\n"
        "Second block — your final output:\n"
        "<conclusion>\n"
        "  Your final analysis and conclusion. This must be consistent with your thought_log. "
        "Include all citations in the format [SOURCE: <label>, <url or N/A>].\n"
        "</conclusion>\n\n"
        "DO NOT produce any text outside these two blocks. "
        "DO NOT omit either block. "
        "Structural validation will fail and the run will be retried if you deviate from this format."
    ),
)


# ─────────────────────────────────────────────
# Active directive — always points to current deployment version
# ─────────────────────────────────────────────

ACTIVE_DIRECTIVE: DirectiveVersion = DIRECTIVE_V1_0_0


def get_directive(version: str) -> DirectiveVersion:
    """Retrieve a directive by version string. Used for historical run comparison."""
    if version not in _DIRECTIVE_REGISTRY:
        raise KeyError(f"Unknown directive version: {version!r}")
    return _DIRECTIVE_REGISTRY[version]


def get_active_directive() -> DirectiveVersion:
    """Return the currently active directive. This is what C-01 injects into every agent prompt."""
    return ACTIVE_DIRECTIVE
