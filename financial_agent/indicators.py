"""
Technical indicators, computed in code from a frozen price snapshot, never by the model.

Why this exists
    A language model can't reliably do arithmetic over a price series. PLAN.md (4.2) has the
    agent ask for indicator values through a tool and interpret them, so every number it cites
    can be checked against what it was given (Tier 1, PLAN.md 6.3).

The rule that matters most: no value without enough history
    An indicator that needs more bars than the series has returns
    `status: "insufficient_history"` with `needed` and `have`, never a value computed on a
    shorter window. A free Alpha Vantage key gives 100 daily bars (financial_agent/prices.py),
    so a 200-day average can't be computed. It isn't faked: the long windows use the weekly
    series and say so in their name (`sma` with `series: "weekly", period: 40` is a 40-week
    average, not a 200-day one).

    An indicator whose bars cross a date in the series' `suspect_dates` (a possible split; the
    series are unadjusted) returns `status: "possible_split"` with the date.

Definitions (closes are c, highs h, lows l; the last bar is the as-of bar)
    sma(period)            mean of the last `period` closes.
    ema(period)            seeded with the SMA of the first `period` closes, then
                           ema = c * k + ema_prev * (1 - k), k = 2 / (period + 1), over the whole
                           series. Uses every bar, so the value depends on the series' start.
    rsi(period)            Wilder: changes d = c_i - c_{i-1}. The first average gain/loss is the
                           mean over the first `period` changes, then avg = (avg_prev*(period-1) +
                           x) / period. RSI = 100 - 100/(1 + gain/loss); 100 when loss is 0 and
                           gain isn't; 50 when both are 0.
    macd(fast,slow,signal) macd = ema(fast) - ema(slow); signal line = EMA(signal) of the macd
                           line from the first bar where ema(slow) exists; histogram = macd - signal.
    atr(period)            Wilder: TR = max(h - l, |h - c_prev|, |l - c_prev|) from the second
                           bar; the first ATR is the mean of the first `period` TRs, then smoothed
                           like RSI.
    volatility(window)     sample standard deviation (n - 1) of the last `window` log returns,
                           annualized (x sqrt(252) daily, x sqrt(52) weekly), in percent.
    return(bars)           (c_last / c_{last - bars} - 1) x 100.
    range(bars)            highest high and lowest low of the last `bars` bars, and the last
                           close's distance from each in percent. The weekly series' last bar is
                           the week in progress, so a 52-bar weekly range runs to the as-of day.
    max_drawdown(bars)     the largest fall from a running peak close to a later close, over the
                           last `bars` closes, in percent (0 or negative).
    last_close             the as-of bar's close and date.

Values are rounded to 4 decimals, so a snapshot hashes the same on every machine.
"""

from __future__ import annotations

import math
from typing import Any, Callable

_ROUND = 4
_ANNUALIZE = {"daily": 252, "weekly": 52}
SERIES = ("daily", "weekly")

# The panel an agent gets when it doesn't ask for anything specific (PLAN.md 4.2, adapted
# 2026-10-06 to what a free key serves: the long windows are weekly).
STANDARD_PANEL: tuple[tuple[str, dict], ...] = (
    ("last_close", {}),
    ("sma", {"period": 20}),
    ("sma", {"period": 50}),
    ("ema", {"period": 12}),
    ("ema", {"period": 26}),
    ("rsi", {"period": 14}),
    ("macd", {}),
    ("atr", {"period": 14}),
    ("volatility", {"window": 30}),
    ("volatility", {"window": 90}),
    ("return", {"bars": 21}),
    ("return", {"bars": 63}),
    ("return", {"series": "weekly", "bars": 26}),
    ("return", {"series": "weekly", "bars": 52}),
    ("sma", {"series": "weekly", "period": 40}),
    ("range", {"series": "weekly", "bars": 52}),
    ("max_drawdown", {"series": "weekly", "bars": 52}),
)


class IndicatorError(ValueError):
    """A request that can't be answered as asked: unknown name, bad parameter."""


def _r(x: float) -> float:
    return round(x, _ROUND)


# ── The calculations (lists of floats in, numbers out; no knowledge of snapshots) ──

def sma(closes: list[float], period: int) -> float:
    return sum(closes[-period:]) / period


def ema_series(values: list[float], period: int) -> list[float]:
    """EMA from index period-1 onward (so len(values) - period + 1 values)."""
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _wilder(xs: list[float], period: int) -> float:
    avg = sum(xs[:period]) / period
    for x in xs[period:]:
        avg = (avg * (period - 1) + x) / period
    return avg


def rsi(closes: list[float], period: int) -> float:
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = _wilder([max(d, 0.0) for d in changes], period)
    loss = _wilder([max(-d, 0.0) for d in changes], period)
    if loss == 0:
        return 50.0 if gain == 0 else 100.0
    return 100 - 100 / (1 + gain / loss)


def macd(closes: list[float], fast: int, slow: int, signal: int) -> dict[str, float]:
    f, s = ema_series(closes, fast), ema_series(closes, slow)
    line = [a - b for a, b in zip(f[slow - fast:], s)]
    sig = ema_series(line, signal)
    return {"macd": line[-1], "signal": sig[-1], "histogram": line[-1] - sig[-1]}


def atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    trs = [max(h - l, abs(h - pc), abs(l - pc))
           for h, l, pc in zip(highs[1:], lows[1:], closes[:-1])]
    return _wilder(trs, period)


def volatility(closes: list[float], window: int, per_year: int) -> float:
    rets = [math.log(b / a) for a, b in zip(closes[-window - 1:], closes[-window:])]
    mean = sum(rets) / window
    var = sum((x - mean) ** 2 for x in rets) / (window - 1)
    return math.sqrt(var) * math.sqrt(per_year) * 100


def pct_return(closes: list[float], bars: int) -> float:
    return (closes[-1] / closes[-1 - bars] - 1) * 100


def max_drawdown(closes: list[float]) -> float:
    peak, worst = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        worst = min(worst, (c / peak - 1) * 100)
    return worst


# ── The registry: name -> (default params, bars needed, unit, compute) ─────────

def _int_param(params: dict, name: str, lo: int, hi: int) -> int:
    v = params[name]
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise IndicatorError(f"{name} must be a whole number from {lo} to {hi}, got {v!r}")
    return v


Spec = tuple[dict, Callable[[dict], int], str, Callable[[dict, dict], dict]]

_SPECS: dict[str, Spec] = {
    "last_close": ({}, lambda p: 1, "USD",
                   lambda b, p: {"close": b["c"][-1], "date": b["d"][-1]}),
    "sma": ({"period": 20}, lambda p: p["period"], "USD",
            lambda b, p: {"value": sma(b["c"], p["period"])}),
    "ema": ({"period": 12}, lambda p: p["period"], "USD",
            lambda b, p: {"value": ema_series(b["c"], p["period"])[-1]}),
    "rsi": ({"period": 14}, lambda p: p["period"] + 1, "index",
            lambda b, p: {"value": rsi(b["c"], p["period"])}),
    "macd": ({"fast": 12, "slow": 26, "signal": 9}, lambda p: p["slow"] + p["signal"] - 1, "USD",
             lambda b, p: macd(b["c"], p["fast"], p["slow"], p["signal"])),
    "atr": ({"period": 14}, lambda p: p["period"] + 1, "USD",
            lambda b, p: {"value": atr(b["h"], b["l"], b["c"], p["period"])}),
    "volatility": ({"window": 30}, lambda p: p["window"] + 1, "pct_annualized",
                   lambda b, p: {"value": volatility(b["c"], p["window"], _ANNUALIZE[b["series"]])}),
    "return": ({"bars": 21}, lambda p: p["bars"] + 1, "pct",
               lambda b, p: {"value": pct_return(b["c"], p["bars"])}),
    "range": ({"bars": 52}, lambda p: p["bars"], "USD",
              lambda b, p: _range(b, p["bars"])),
    "max_drawdown": ({"bars": 52}, lambda p: p["bars"], "pct",
                     lambda b, p: {"value": max_drawdown(b["c"][-p["bars"]:])}),
}
INDICATORS = tuple(_SPECS)

# Which bars each indicator's value depends on, for the split check: the EMA family and the
# Wilder averages use the whole series; the rest use the last `needed` bars.
_WHOLE_SERIES = {"ema", "rsi", "macd", "atr"}


def _range(b: dict, bars: int) -> dict:
    hi, lo, c = max(b["h"][-bars:]), min(b["l"][-bars:]), b["c"][-1]
    return {"high": hi, "low": lo, "pct_from_high": (c / hi - 1) * 100, "pct_from_low": (c / lo - 1) * 100}


def _resolve(name: str, params: dict | None) -> dict:
    if name not in _SPECS:
        raise IndicatorError(f"unknown indicator {name!r}; known: {', '.join(INDICATORS)}")
    params = dict(params or {})
    series = params.pop("series", "daily")
    if series not in SERIES:
        raise IndicatorError(f"series must be one of {', '.join(SERIES)}, got {series!r}")
    defaults = _SPECS[name][0]
    unknown = set(params) - set(defaults)
    if unknown:
        raise IndicatorError(f"{name} takes {sorted(defaults) or 'no parameters'}, not {sorted(unknown)}")
    full = {**defaults, **params}
    for key in full:
        _int_param(full, key, 1 if key != "window" else 2, 2000)
    if name == "macd" and not full["fast"] < full["slow"]:
        raise IndicatorError("macd needs fast < slow")
    return {"series": series, **full}


def compute(snapshot: dict, name: str, params: dict | None = None) -> dict:
    """
    One indicator over one series of a price snapshot. Raises IndicatorError for a request
    that's malformed; returns a result with a status for one the data can't answer.
    """
    full = _resolve(name, params)
    series = full["series"]
    calc_params = {k: v for k, v in full.items() if k != "series"}
    _, needed_fn, unit, fn = _SPECS[name]
    data = snapshot["series"][series]
    rows = data["rows"]
    needed = needed_fn(calc_params)
    base = {"name": name, "params": full, "series": series, "unit": unit,
            "as_of": rows[-1][0] if rows else None}
    if len(rows) < needed:
        return {**base, "status": "insufficient_history", "needed": needed, "have": len(rows)}

    used = rows if name in _WHOLE_SERIES else rows[-needed:]
    # A suspect date is the second bar of a jump, so it only matters if the bar before it is
    # in the window too.
    crossed = [d for d in data.get("suspect_dates", []) if used[0][0] < d <= used[-1][0]]
    if crossed:
        return {**base, "status": "possible_split", "split_date": crossed[-1],
                "bars_used": len(used)}

    b = {"series": series, "d": [r[0] for r in rows], "h": [r[2] for r in rows],
         "l": [r[3] for r in rows], "c": [r[4] for r in rows]}
    values = fn(b, calc_params)
    values = {k: (_r(v) if isinstance(v, float) else v) for k, v in values.items()}
    return {**base, "status": "ok", "bars_used": len(used), "values": values}


def standard_panel(snapshot: dict) -> list[dict]:
    return [compute(snapshot, name, params) for name, params in STANDARD_PANEL]


def describe(result: dict) -> str:
    """One line for a trace or a prompt: 'rsi(period=14, daily) = 61.2 index'."""
    p = ", ".join(f"{k}={v}" for k, v in result["params"].items() if k != "series")
    head = f"{result['name']}({p}{', ' if p else ''}{result['series']})"
    if result["status"] != "ok":
        extra = (f"needs {result['needed']} bars, have {result['have']}"
                 if result["status"] == "insufficient_history" else f"crosses {result['split_date']}")
        return f"{head}: {result['status']} ({extra})"
    vals: Any = result["values"]
    shown = vals["value"] if set(vals) == {"value"} else vals
    return f"{head} = {shown} {result['unit']}"
