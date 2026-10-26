"""
The data tools: the only way an analysis agent sees data (PLAN.md 5, D11).

Why one implementation
    The built-in agent calls these in-process. Session agents (Claude Code, Codex) will call
    the same functions over /mcp (phase 5), and agent B in a verification replays them over a
    stored bundle (financial_agent/replay.py). Because every path runs these exact functions,
    "what the agent was given" has one meaning everywhere.

Each tool is a pure function of (bundle data, arguments) -> result. None of them touch the
network: the data was fetched before the run started, and is frozen in the bundle. A request
the bundle can't answer gets an {"error": ...} result, which is recorded like any other
result. It is never fetched fresh, and never answered with an invented value.

ToolRecorder wraps the tools for one run. It numbers each call (seq 1, 2, 3 ... with no gaps,
across both agents' slots), keeps the exact result the agent was given and that result's
hash, and is thread-safe, because two agents may call tools at the same time.

Document tools (search_doc, read_doc_chunk) arrive with uploads in phase 6.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from core.analysis import content_hash
from financial_agent import indicators

MAX_ROWS = 260          # the most bars one get_prices call returns
DEFAULT_ROWS = 30
MAX_INDICATORS = 30     # per get_indicators call

# What an agent is told about each tool. The parameter notes are the contract the
# argument checks below enforce.
TOOL_SPECS: dict[str, dict[str, Any]] = {
    "describe_data": {
        "description": "What data this analysis has: ticker, as-of date, how many daily and weekly "
                       "bars, possible split dates, which filing facts exist, which indicators can be computed.",
        "params": {},
    },
    "get_prices": {
        "description": "End-of-day price bars (date, open, high, low, close, volume), oldest first, "
                       "unadjusted for splits and dividends.",
        "params": {"series": "'daily' (default) or 'weekly'",
                   "last": f"how many of the most recent bars, 1-{MAX_ROWS} (default {DEFAULT_ROWS})"},
    },
    "get_indicators": {
        "description": "Technical indicators computed in code from the price bars. With no "
                       "'requests', returns the standard panel.",
        "params": {"requests": "optional list of {\"name\": ..., \"params\": {...}}; names: "
                               + ", ".join(indicators.INDICATORS)
                               + "; every indicator also takes params.series 'daily' or 'weekly'"},
    },
    "get_filing_facts": {
        "description": "The company's most recent reported figures from SEC EDGAR, with period, "
                       "form and accession number.",
        "params": {"concepts": "optional list of us-gaap concept names to keep (default: all)"},
    },
}
TOOLS = tuple(TOOL_SPECS)


class ToolArgumentError(ValueError):
    pass


def _check_args(tool: str, args: Any) -> dict:
    if not isinstance(args, dict):
        raise ToolArgumentError("arguments must be an object")
    unknown = set(args) - set(TOOL_SPECS[tool]["params"])
    if unknown:
        raise ToolArgumentError(f"{tool} takes {sorted(TOOL_SPECS[tool]['params']) or 'no arguments'}, "
                                f"not {sorted(unknown)}")
    return args


def describe_data(data: dict, args: dict) -> dict:
    prices = data["prices"]
    filing = data.get("filing")
    return {
        "ticker": data["ticker"],
        "as_of": data["as_of"],
        "price_source": prices["source"],
        "adjusted": prices["adjusted"],
        "series": {name: {"bars": len(s["rows"]), "first": s["rows"][0][0], "last": s["rows"][-1][0],
                          "possible_split_dates": s["suspect_dates"]}
                   for name, s in prices["series"].items()},
        "filing": ({"concepts": [f["concept"] for f in filing["facts"]], "missing": filing["missing"],
                    "latest_period_end": filing["latest_period_end"],
                    "older_than_latest_period": filing["stale"]}
                   if filing else "no SEC filing data in this analysis"),
        "documents": len(data.get("documents") or []),
        "indicators": list(indicators.INDICATORS),
    }


def get_prices(data: dict, args: dict) -> dict:
    series = args.get("series", "daily")
    if series not in indicators.SERIES:
        raise ToolArgumentError(f"series must be one of {', '.join(indicators.SERIES)}")
    last = args.get("last", DEFAULT_ROWS)
    if not isinstance(last, int) or isinstance(last, bool) or not 1 <= last <= MAX_ROWS:
        raise ToolArgumentError(f"last must be a whole number from 1 to {MAX_ROWS}")
    rows = data["prices"]["series"][series]["rows"][-last:]
    return {"series": series, "as_of": data["as_of"], "adjusted": data["prices"]["adjusted"],
            "columns": ["date", "open", "high", "low", "close", "volume"], "rows": rows}


def get_indicators(data: dict, args: dict) -> dict:
    requests = args.get("requests")
    if requests is None:
        return {"as_of": data["as_of"], "results": indicators.standard_panel(data["prices"])}
    if not isinstance(requests, list) or not requests:
        raise ToolArgumentError("requests must be a non-empty list")
    if len(requests) > MAX_INDICATORS:
        raise ToolArgumentError(f"at most {MAX_INDICATORS} indicators per call")
    results = []
    for i, req in enumerate(requests, 1):
        if not isinstance(req, dict) or not isinstance(req.get("name"), str):
            raise ToolArgumentError(f"request {i} must be {{\"name\": ..., \"params\": {{...}}}}")
        params = req.get("params") or {}
        if not isinstance(params, dict):
            raise ToolArgumentError(f"request {i}: params must be an object")
        try:
            results.append(indicators.compute(data["prices"], req["name"], params))
        except indicators.IndicatorError as exc:
            raise ToolArgumentError(f"request {i}: {exc}") from None
    return {"as_of": data["as_of"], "results": results}


def get_filing_facts(data: dict, args: dict) -> dict:
    filing = data.get("filing")
    if not filing:
        raise ToolArgumentError("this analysis has no SEC filing data")
    concepts = args.get("concepts")
    facts = filing["facts"]
    if concepts is not None:
        if not isinstance(concepts, list) or not all(isinstance(c, str) for c in concepts):
            raise ToolArgumentError("concepts must be a list of names")
        facts = [f for f in facts if f["concept"] in concepts]
    return {"entity": filing.get("entity"), "cik": filing.get("cik"), "source": filing["source"],
            "latest_period_end": filing["latest_period_end"], "facts": facts, "missing": filing["missing"],
            "note": "a fact with stale: true ends before latest_period_end; it is not the current period"}


_IMPL: dict[str, Callable[[dict, dict], dict]] = {
    "describe_data": describe_data,
    "get_prices": get_prices,
    "get_indicators": get_indicators,
    "get_filing_facts": get_filing_facts,
}


def run_tool(data: dict, tool: str, args: Any) -> dict:
    """The result an agent gets: the tool's output, or {"error": ...}. Never raises for a bad request."""
    if tool not in _IMPL:
        return {"error": f"unknown tool {tool!r}; tools: {', '.join(TOOLS)}"}
    try:
        return _IMPL[tool](data, _check_args(tool, args))
    except ToolArgumentError as exc:
        return {"error": str(exc)}


class ToolRecorder:
    """The tools for one run over one bundle, every call recorded."""

    def __init__(self, bundle: dict):
        self.bundle_hash = bundle["bundle_hash"]
        self._data = bundle["data"]
        self._lock = threading.Lock()
        self.calls: list[dict] = []

    def call(self, slot: str, tool: str, args: Any = None) -> dict:
        args = {} if args is None else args
        result = run_tool(self._data, tool, args)
        with self._lock:
            entry = {"seq": len(self.calls) + 1, "slot": slot, "tool": tool, "args": args,
                     "result": result, "result_sha256": content_hash(result)}
            self.calls.append(entry)
        return result

    def calls_for(self, slot: str) -> list[dict]:
        with self._lock:
            return [c for c in self.calls if c["slot"] == slot]
