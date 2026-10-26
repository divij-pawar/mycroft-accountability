"""
The analysis record: the one contract between the financial agent and the verification layer.

Why this exists
    financial_agent/ produces signals; validation/ checks them. financial_agent/PLAN.md (D1)
    keeps the two components apart, so that the agent can later move out of this directory: neither
    imports the other (tests/test_layering.py). What they share is the shape of what one writes
    and the other reads, and that shape lives here, in core/, which both may import.

What this module holds
    - The closed vocabularies: the 5-point signal and its display letters (A-E), horizons,
      conviction, agent kinds, reasoning modes, evidence tiers, verdicts.
    - validate_signal(): the strict check of an agent's signal object. A value outside the
      vocabulary is a recorded issue, never coerced into the nearest valid one (P3), the same
      rule as core/assessment.py: "Buy" is not turned into "buy", "A" is not turned into
      "strong_buy". Only valid fields are kept.
    - canonical_json() / content_hash(): the one hashing rule, so the agent that builds a
      bundle and the layer that later checks it compute the same hash.
    - TypedDicts documenting the record shapes. They aren't enforced at runtime; the
      store's CHECK constraints and validate_signal() are what enforce.

The signal is a model's judgment about a stock, not a verified fact (P8), and not investment
advice. The letter is a display alias of the stored ordinal (PLAN.md 6.2, decided 2026-10-06):
agents are never asked for a letter, so they can't return one outside the vocabulary.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict


# ── Vocabularies ───────────────────────────────────────────────────────────────

# Ordinal, lowest first: the index is the rank, so distance is a subtraction.
SIGNALS = ("strong_sell", "sell", "hold", "buy", "strong_buy")
SIGNAL_LETTERS = {"strong_buy": "A", "buy": "B", "hold": "C", "sell": "D", "strong_sell": "E"}
SIGNAL_WORDS = {"strong_buy": "Strong buy", "buy": "Buy", "hold": "Hold", "sell": "Sell",
                "strong_sell": "Strong sell"}

HORIZONS = ("1-3m", "3-6m", "6-12m", "12m+")
HORIZON_WORDS = {"1-3m": "1–3 months", "3-6m": "3–6 months", "6-12m": "6–12 months",
                 "12m+": "12 months or more"}
DEFAULT_HORIZON = "3-6m"   # when the user names none (PLAN.md 6.2, decided 2026-10-06)

CONVICTIONS = ("low", "medium", "high")
AGENT_KINDS = ("builtin", "session", "byo")
REASONING_MODES = ("native", "none")
RUN_STATUSES = ("ok", "halted", "abstained")

# Where a cited figure came from. "document" is user-asserted: shown, compared, never a pass.
EVIDENCE_TIERS = ("data", "filing", "document", "judgment")

# The Tier-2 verdict (PLAN.md 6.4), in precedence order: the first that applies wins.
VERDICTS = ("UNGROUNDED", "INCONCLUSIVE", "DIVERGENT", "ADJACENT", "CONSISTENT")

MAX_KEY_POINTS = 3
MAX_POINT_CHARS = 400
MAX_CITED_FIGURES = 40

BUNDLE_SCHEMA = "bundle-v1"
RECORD_SCHEMA = "analysis-v1"


def signal_rank(signal: str) -> int:
    """0 (strong_sell) to 4 (strong_buy). Raises ValueError outside the vocabulary."""
    return SIGNALS.index(signal)


def signal_distance(a: str, b: str) -> int:
    """How many levels apart two signals are: 0 is the same signal, 4 is opposite extremes."""
    return abs(signal_rank(a) - signal_rank(b))


def signal_letter(signal: str) -> str:
    return SIGNAL_LETTERS[signal]


def signal_label(signal: str) -> str:
    """'B · Buy', the headline form."""
    return f"{SIGNAL_LETTERS[signal]} · {SIGNAL_WORDS[signal]}"


# ── Hashing ────────────────────────────────────────────────────────────────────

def canonical_json(obj: Any) -> str:
    """
    The one serialization every hash is taken over: sorted keys, no whitespace, UTF-8 kept.
    NaN and infinity are refused (allow_nan=False). They have no JSON form, and a bundle
    holding one would hash differently in other languages.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_hash(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ── Record shapes (documentation; see the module docstring) ────────────────────

class ToolCall(TypedDict):
    seq: int                 # 1, 2, 3 ... within one run, gap-free
    slot: str                # which agent made it ("a", "b", or "a" for a single analysis)
    tool: str
    args: dict
    result: dict             # exactly what the agent was given, an {"error": ...} included
    result_sha256: str       # content_hash(result)


class Bundle(TypedDict):
    bundle_hash: str         # content_hash(data)
    created_at: str          # not hashed: the same data fetched twice is the same bundle
    data: dict               # {"schema", "ticker", "as_of", "prices", "filing", "documents"}


class CitedFigure(TypedDict, total=False):
    name: str
    value: float
    unit: str
    ref: dict                # exactly one of {"call": seq}, {"chunk": id}, {"judgment": True}


class AnalysisRecord(TypedDict, total=False):
    schema: str
    run_id: str
    parent_run_id: str | None
    created_at: str
    request: dict            # {"message", "ticker", "horizon", "horizon_defaulted"}
    agent: dict              # {"kind", "model", "reasoning", "prompt_version", "temperature", "seed", "label"}
    bundle_hash: str
    data_as_of: str          # the date of the newest price bar the agent could see
    inputs_captured: bool
    tool_calls: list[ToolCall]
    steps: list[dict]
    attempts: list[dict]     # {"attempt", "parse_status", "raw_text", "directive_version"}, every one kept
    corrective_text: str | None   # what the second attempt was told, when there was one
    output: dict | None      # validate_signal()'s kept fields; None when halted
    conclusion: str | None
    thought_log: str | None
    issues: list[str]        # what validate_signal() dropped or what was filled in, in words
    halt_reason: str | None
    status: str


class VerdictRecord(TypedDict, total=False):
    verdict_id: str
    run_id: str
    bundle_hash: str
    created_at: str
    agents: list[dict]
    tier1: list[dict]
    signals: list[str]
    distance: int | None
    divergence_class: str | None
    verdict: str
    reasons: list[str]


# ── The signal object ──────────────────────────────────────────────────────────

Status = Literal["valid", "partial", "invalid"]


@dataclass
class ParsedSignal:
    """
    valid: every field usable. partial: the signal itself is usable, some other fields
    were dropped, with issues listed. invalid: no usable signal (the rest isn't kept
    either, since a key point or figure without its signal says nothing).
    """
    status: Status
    signal: dict | None
    issues: list[str] = field(default_factory=list)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _valid_ref(ref: Any) -> str | None:
    """None if `ref` is valid, else what is wrong with it."""
    if not isinstance(ref, dict) or len(ref) != 1:
        return 'must be exactly one of {"call": n}, {"chunk": "id"}, {"judgment": true}'
    (kind, value), = ref.items()
    if kind == "call":
        ok = isinstance(value, int) and not isinstance(value, bool) and value >= 1
    elif kind == "chunk":
        ok = isinstance(value, str) and bool(value.strip())
    elif kind == "judgment":
        ok = value is True
    else:
        return f"unknown reference kind {kind!r}"
    return None if ok else f"invalid {kind!r} reference {value!r}"


def validate_signal(obj: Any) -> ParsedSignal:
    """
    Check an agent's signal object against the vocabulary, field by field.

        {"signal": "buy", "horizon": "3-6m", "conviction": "medium",
         "key_points": [{"text": "...", "evidence": [{"call": 3}]}],
         "cited_figures": [{"name": "rsi_14", "value": 71.2, "unit": "index", "ref": {"call": 3}}]}

    `signal` is required. `horizon` and `conviction` are dropped (with an issue) if invalid.
    A key point or figure that fails is dropped on its own; the others are kept. Unknown
    top-level keys are reported and dropped.
    """
    issues: list[str] = []
    if not isinstance(obj, dict):
        return ParsedSignal("invalid", None, ["the signal must be a JSON object"])

    known = {"signal", "horizon", "conviction", "key_points", "cited_figures"}
    for key in sorted(set(obj) - known):
        issues.append(f"unknown field {key!r} dropped")

    signal = obj.get("signal")
    if signal not in SIGNALS:
        return ParsedSignal("invalid", None, issues + [
            f"signal {signal!r} is not one of {', '.join(SIGNALS)}"])
    out: dict[str, Any] = {"signal": signal}

    if "horizon" in obj:
        if obj["horizon"] in HORIZONS:
            out["horizon"] = obj["horizon"]
        else:
            issues.append(f"horizon {obj['horizon']!r} is not one of {', '.join(HORIZONS)}; dropped")
    if "conviction" in obj:
        if obj["conviction"] in CONVICTIONS:
            out["conviction"] = obj["conviction"]
        else:
            issues.append(f"conviction {obj['conviction']!r} is not one of {', '.join(CONVICTIONS)}; dropped")

    points = obj.get("key_points", [])
    if not isinstance(points, list):
        issues.append("key_points must be a list; dropped")
        points = []
    if len(points) > MAX_KEY_POINTS:
        issues.append(f"{len(points)} key points given, only the first {MAX_KEY_POINTS} kept")
        points = points[:MAX_KEY_POINTS]
    kept_points = []
    for i, p in enumerate(points, 1):
        text = p.get("text") if isinstance(p, dict) else None
        if not isinstance(text, str) or not text.strip():
            issues.append(f"key point {i} has no text; dropped")
            continue
        if len(text) > MAX_POINT_CHARS:
            issues.append(f"key point {i} is longer than {MAX_POINT_CHARS} characters; dropped")
            continue
        evidence = p.get("evidence", [])
        if not isinstance(evidence, list):
            issues.append(f"key point {i}: evidence must be a list; dropped")
            continue
        bad = [why for why in map(_valid_ref, evidence) if why]
        if bad:
            issues.append(f"key point {i}: evidence {bad[0]}; dropped")
            continue
        kept_points.append({"text": text.strip(), "evidence": evidence})
    out["key_points"] = kept_points

    figures = obj.get("cited_figures", [])
    if not isinstance(figures, list):
        issues.append("cited_figures must be a list; dropped")
        figures = []
    if len(figures) > MAX_CITED_FIGURES:
        issues.append(f"{len(figures)} figures given, only the first {MAX_CITED_FIGURES} kept")
        figures = figures[:MAX_CITED_FIGURES]
    kept_figures = []
    for i, f in enumerate(figures, 1):
        if not isinstance(f, dict):
            issues.append(f"figure {i} is not an object; dropped")
            continue
        name, value = f.get("name"), f.get("value")
        if not isinstance(name, str) or not name.strip():
            issues.append(f"figure {i} has no name; dropped")
            continue
        if not _number(value):
            issues.append(f"figure {i} ({name}): value {value!r} is not a number; dropped")
            continue
        why = _valid_ref(f.get("ref"))
        if why:
            issues.append(f"figure {i} ({name}): ref {why}; dropped")
            continue
        unit = f.get("unit")
        if unit is not None and not isinstance(unit, str):
            issues.append(f"figure {i} ({name}): unit must be text; dropped")
            continue
        kept = {"name": name.strip(), "value": value, "ref": f["ref"]}
        if unit:
            kept["unit"] = unit
        kept_figures.append(kept)
    out["cited_figures"] = kept_figures

    return ParsedSignal("partial" if issues else "valid", out, issues)
