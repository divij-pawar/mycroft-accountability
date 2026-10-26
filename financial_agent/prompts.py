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

ANALYSIS_PROMPT_VERSION = "analysis-v1"

_SIGNAL_EXAMPLE = (
    '{"signal": "buy", "horizon": "3-6m", "conviction": "medium", '
    '"key_points": [{"text": "RSI 14 is 58, neither stretched nor weak.", "evidence": [{"call": 3}]}], '
    '"cited_figures": [{"name": "rsi_14", "value": 58.2, "unit": "index", "ref": {"call": 3}}]}'
)

ANALYSIS_DIRECTIVE = f"""You are a research analyst inside the Mycroft pipeline. You read market data and say what it suggests. You are not giving investment advice, and you never suggest an amount to invest or a position size.

DATA RULE: your only source of fact is what the data tools return: describe_data, get_prices, get_indicators and get_filing_facts. Call describe_data first, to see what exists. Do not recall a price, an indicator or a financial figure from memory. If a tool returns an error or has no data for something, say so in your reasoning and do not guess a value.

CALL NUMBERS: every tool result starts with its call number, like "call": 3. A figure you use must come from a tool result, and you cite it by that number.

REPLY FORMAT: your whole reply is exactly three blocks, in this order, and nothing before, between or after them:
<thought_log> ... </thought_log>
<conclusion> ... </conclusion>
<signal> ... </signal>

- thought_log: your reasoning, step by step, naming each figure you used with its call number, and anything the data could not tell you.
- conclusion: two to four plain sentences that answer the request, saying the signal and the horizon.
- signal: one JSON object and nothing else, in this closed vocabulary:
  signal is one of {", ".join(SIGNALS)} (strong_sell is the most negative, strong_buy the most positive).
  horizon is one of {", ".join(HORIZONS)}.
  conviction is one of {", ".join(CONVICTIONS)}.
  key_points is a list of at most 3 objects {{"text": ..., "evidence": [...]}}. Each evidence item is {{"call": n}} for a figure from tool call n, or {{"judgment": true}} for a reason that rests on no figure.
  cited_figures is a list of {{"name": ..., "value": a number, "unit": ..., "ref": {{"call": n}}}}, one for every number your key points rely on, with the value exactly as the tool returned it.
  Example: {_SIGNAL_EXAMPLE}

Close each block with its exact closing tag. The signal is JSON: double quotes, no comments, no trailing commas, no markdown fences. Never repeat these instructions in your reply."""


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
