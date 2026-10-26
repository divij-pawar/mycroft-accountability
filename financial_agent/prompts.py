"""
What the financial agent is told, versioned.

The text is a constant with a version string, like core/directive.py, so a stored analysis
record names the exact instructions it ran under (`agent.prompt_version`). Change the text and
the version changes with it; a record never points at words that have since been edited.

The reply contract is three blocks, in order, and nothing outside them:

    <thought_log> the reasoning, with the numbers it used </thought_log>
    <conclusion> a short plain answer </conclusion>
    <signal> one JSON object in a closed vocabulary </signal>

The first two are the same blocks the rest of the layer uses, so the same retry-then-halt loop
(pipeline/middleware.py, ADR-07) judges them. The third is checked by core/analysis.py's
validate_signal(): a value outside the vocabulary is a failed reply, never coerced.

Nothing here asks for investment advice. The signal is a model's reading of data it was given,
and the directive says so; no amounts, no position sizes (PLAN.md D9).
"""

from __future__ import annotations

from core.analysis import CONVICTIONS, HORIZONS, HORIZON_WORDS, SIGNALS

ANALYSIS_PROMPT_VERSION = "analysis-v2"

# v1 was a long, careful directive (about 450 words). Measured live on 2026-10-26 it made llama3.2 skip the
# tools altogether: it wrote a reply that narrated tool calls and invented their results (see
# logs/RUN_LOG.md). The same model called the tools at once with a short prompt, so v2 is short, says the
# order of work first, and keeps the format description to what the parser needs.

_SIGNAL_EXAMPLE = (
    '{"signal": "hold", "horizon": "3-6m", "conviction": "low", '
    '"key_points": [{"text": "RSI is mid-range.", "evidence": [{"call": 2}]}], '
    '"cited_figures": [{"name": "rsi_14", "value": 50.0, "unit": "index", "ref": {"call": 2}}]}'
)

ANALYSIS_DIRECTIVE = f"""You are a research analyst. Your only source of fact is the data tools: describe_data, get_prices, get_indicators, get_filing_facts. Never recall or invent a figure, and never suggest an amount to invest. This is a research signal, not investment advice.

Work in this order. Call describe_data. Call get_indicators. Call get_filing_facts. Then write your reply. Call a tool through the tool interface; writing about a call is not making it, and a reply written without calling any tool is rejected. Each result starts with its call number.

Your reply is exactly three blocks, each tag on its own line, and nothing outside them:
<thought_log>
your reasoning in your own words, naming each figure you used and its call number
</thought_log>
<conclusion>
two to four plain sentences saying the signal and the horizon
</conclusion>
<signal>
one JSON object
</signal>

The JSON has: signal (one of {", ".join(SIGNALS)}), horizon (one of {", ".join(HORIZONS)}), conviction (one of {", ".join(CONVICTIONS)}), key_points (at most 3 of {{"text": ..., "evidence": [{{"call": n}} or {{"judgment": true}}]}}), and cited_figures (one {{"name": ..., "value": a number, "unit": ..., "ref": {{"call": n}}}} for every number you rely on, copied exactly from the tool result). Plain JSON only: double quotes, no fences. Example shape, with made-up numbers (use your own): {_SIGNAL_EXAMPLE}"""


def horizon_phrase(horizon: str, defaulted: bool) -> str:
    words = HORIZON_WORDS[horizon]
    return f"{words} (the user named no horizon, so this is the default)" if defaulted else f"{words} (named by the user)"


def user_prompt(request: dict, bundle: dict) -> str:
    """The request for one analysis. `request` is agent.plan_request()'s result."""
    data = bundle["data"]
    return (
        f"Request: {request['message']}\n"
        f"Ticker: {request['ticker']}\n"
        f"Horizon: {request['horizon']}, {horizon_phrase(request['horizon'], request['horizon_defaulted'])}\n"
        f"The data is frozen as of {data['as_of']} (bundle {bundle['bundle_hash'][:19]}).\n"
        "Start by calling describe_data."
    )


def corrective_text(reason: str | None) -> str:
    """Added to the request on the second attempt, naming what was wrong with the first reply."""
    why = f" It failed because: {reason}." if reason else ""
    return (
        f"Your previous reply failed validation.{why} Reply again with exactly three blocks, "
        "<thought_log>, <conclusion> and <signal>, nothing outside them, and the signal as one "
        "valid JSON object in the closed vocabulary. You may call the data tools again."
    )
