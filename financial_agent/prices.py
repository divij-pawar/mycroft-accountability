"""
End-of-day stock prices from Alpha Vantage: the financial agent's market data, and a network
egress point (tests/test_layering.py names it).

What a free key gets (observed 2026-10-06, logs/RUN_LOG.md)
    - TIME_SERIES_DAILY with outputsize=compact: the latest 100 daily bars. outputsize=full
      is a premium feature and comes back as an "Information" message, not data.
    - TIME_SERIES_WEEKLY: the full weekly history (1,405 AAPL bars, back to 1999).
    - 25 requests a day and 1 a second. Past either limit the API still answers HTTP 200, with
      an "Information" message in place of data.

    So one fetch is two requests: daily compact for the short-window indicators, and weekly
    for the long-window ones (52-week range, 6- and 12-month returns, a 40-week average). A
    short series is never stretched to cover a long window (financial_agent/indicators.py).

    Stooq, the plan's first choice, was dropped the same day: its CSV endpoint now answers with
    a JavaScript proof-of-work browser check instead of data. Getting past that would mean
    defeating a bot check, so it isn't attempted.

What a snapshot guarantees
    - The series are as Alpha Vantage sent them, unadjusted for splits and dividends
      (`adjusted: false`). A day-to-day close move beyond the split thresholds below is
      recorded in `suspect_dates`, and an indicator whose window crosses one refuses to give a
      value rather than compute across a split.
    - The API key is never stored. Recorded URLs carry `apikey=REDACTED`, and error text is
      scrubbed of the key before it is raised.
    - An "Information", "Note" or "Error Message" reply is an error with its kind (rate_limited,
      premium, bad_symbol), never an empty series.

Cache: one snapshot per ticker per UTC day under web/data/price_cache/ (gitignored). A fetch
before the US close reuses the previous close until the next UTC day; for end-of-day analysis
that is "recent enough" (PLAN.md 4.1).

stdlib only: urllib, json. No new dependency.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.analysis import content_hash
from core.contracts import JsonFetcher

SOURCE = "alphavantage"
_BASE_URL = "https://www.alphavantage.co/query"
_TIMEOUT_S = 20
_MAX_BYTES = 5_000_000
_PAUSE_S = 1.2                    # between the two requests: the free key allows 1 a second
WEEKLY_KEEP = 156                 # three years of weekly bars; the longest window needs 53
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "web" / "data" / "price_cache"

# A close-to-close move beyond these is recorded as a possible split. Real one-day moves this
# large happen (a crash, a buyout), and those get flagged too: refusing an indicator over one
# is cheaper than silently computing across a 4-for-1 split.
SPLIT_DOWN = 0.6                  # close fell to under 60% of the previous close
SPLIT_UP = 1.6                    # or rose to over 160% (a reverse split)

# Uppercase letters, digits, and one class separator ("BRK.B", "BF-B"). Checked before the
# ticker goes anywhere near a URL.
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,6}(?:[.\-][A-Z0-9]{1,2})?$")


class PriceFetchError(Exception):
    """kind: no_key | bad_ticker | rate_limited | premium | bad_symbol | network | bad_response."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _scrub(text: str, key: str | None) -> str:
    return text.replace(key, "REDACTED") if key else text


def _url(params: dict, key: str) -> tuple[str, str]:
    """(the URL to call, the URL to record), the second with the key redacted."""
    real = _BASE_URL + "?" + urllib.parse.urlencode({**params, "apikey": key})
    shown = _BASE_URL + "?" + urllib.parse.urlencode({**params, "apikey": "REDACTED"})
    return real, shown


def http_get_json(url: str) -> dict:
    """Default fetcher: one HTTPS GET, parsed as JSON. Raises PriceFetchError, key scrubbed."""
    key = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("apikey", [None])[0]
    req = urllib.request.Request(url, headers={"User-Agent": "mycroft-financial-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            body = resp.read(_MAX_BYTES).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PriceFetchError("network", _scrub(f"price request failed: {exc}", key)) from None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise PriceFetchError("bad_response", "the price source did not return JSON") from None


def _api_error(payload: dict) -> PriceFetchError | None:
    """Alpha Vantage reports every failure inside a 200 response."""
    if "Error Message" in payload:
        return PriceFetchError("bad_symbol", str(payload["Error Message"]))
    message = payload.get("Information") or payload.get("Note")
    if message:
        message = str(message)
        kind = "premium" if "premium feature" in message.lower() else "rate_limited"
        return PriceFetchError(kind, message)
    return None


def _rows(series: dict) -> list[list]:
    """Alpha Vantage's {date: {"1. open": "..."}} as [[date, o, h, l, c, v], ...], oldest first."""
    rows = []
    for day in sorted(series):
        bar = series[day]
        try:
            o, h, l, c = (float(bar[k]) for k in ("1. open", "2. high", "3. low", "4. close"))
            v = int(float(bar["5. volume"]))
        except (KeyError, TypeError, ValueError):
            raise PriceFetchError("bad_response", f"malformed bar for {day}") from None
        if min(o, h, l, c) <= 0 or h < l:
            raise PriceFetchError("bad_response", f"impossible bar for {day}: {bar}")
        rows.append([day, o, h, l, c, v])
    return rows


def suspect_dates(rows: list[list]) -> list[str]:
    """Dates whose close moved beyond the split thresholds from the previous bar's close."""
    out = []
    for prev, cur in zip(rows, rows[1:]):
        ratio = cur[4] / prev[4]
        if ratio < SPLIT_DOWN or ratio > SPLIT_UP:
            out.append(cur[0])
    return out


def _series(rows: list[list]) -> dict:
    return {"rows": rows, "rows_sha256": content_hash(rows), "suspect_dates": suspect_dates(rows)}


def snapshot_from_payloads(ticker: str, daily: dict, weekly: dict, *, fetched_at: str,
                           urls: tuple[str, str]) -> dict:
    """Build the snapshot from the two raw replies. Pure: what the tests feed recorded replies to."""
    for payload in (daily, weekly):
        err = _api_error(payload)
        if err:
            raise err
    try:
        daily_rows = _rows(daily["Time Series (Daily)"])
        weekly_rows = _rows(weekly["Weekly Time Series"])[-WEEKLY_KEEP:]
    except KeyError as exc:
        raise PriceFetchError("bad_response", f"reply is missing {exc}") from None
    if not daily_rows or not weekly_rows:
        raise PriceFetchError("bad_response", "the price source returned an empty series")
    meta = daily.get("Meta Data") or {}
    return {
        "ticker": ticker,
        "source": SOURCE,
        "adjusted": False,
        "fetched_at": fetched_at,
        "as_of": daily_rows[-1][0],
        "requests": [
            {"function": "TIME_SERIES_DAILY", "outputsize": "compact", "url": urls[0],
             "last_refreshed": meta.get("3. Last Refreshed"), "time_zone": meta.get("5. Time Zone")},
            {"function": "TIME_SERIES_WEEKLY", "url": urls[1],
             "last_refreshed": (weekly.get("Meta Data") or {}).get("3. Last Refreshed")},
        ],
        "series": {"daily": _series(daily_rows), "weekly": _series(weekly_rows)},
    }


def unavailable_reason(ticker: str, *, now: datetime | None = None, cache_dir: Path | None = DEFAULT_CACHE_DIR) -> str | None:
    """
    None if fetch_prices() could proceed for `ticker` right now (today's snapshot is cached, or a key
    is set), else why not. Lets a route refuse with a clear message before it starts streaming.
    """
    try:
        ticker = normalize_ticker(ticker)
    except PriceFetchError as exc:
        return str(exc)
    now = now or datetime.now(timezone.utc)
    if cache_dir and (cache_dir / f"{ticker}_{now.strftime('%Y-%m-%d')}.json").is_file():
        return None
    if not os.environ.get("ALPHAVANTAGE_API_KEY"):
        return "ALPHAVANTAGE_API_KEY is not set (see .env.example), and there is no cached snapshot for today"
    return None


def normalize_ticker(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if not TICKER_RE.match(t):
        raise PriceFetchError("bad_ticker", f"{ticker!r} doesn't look like a US ticker")
    return t


def fetch_prices(
    ticker: str,
    *,
    api_key: str | None = None,
    fetch_json: JsonFetcher | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
) -> dict:
    """
    The day's price snapshot for `ticker`: from the cache if today's exists, else two
    requests. `api_key` defaults to ALPHAVANTAGE_API_KEY. `cache_dir=None` turns the cache off.
    Raises PriceFetchError, never returns a partial snapshot.
    """
    ticker = normalize_ticker(ticker)
    now = now or datetime.now(timezone.utc)
    cache_file = cache_dir / f"{ticker}_{now.strftime('%Y-%m-%d')}.json" if cache_dir else None
    if cache_file and cache_file.is_file():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    key = api_key if api_key is not None else os.environ.get("ALPHAVANTAGE_API_KEY", "")
    if not key:
        raise PriceFetchError("no_key", "ALPHAVANTAGE_API_KEY is not set (see .env.example)")
    fetch = fetch_json or http_get_json

    daily_url, daily_shown = _url({"function": "TIME_SERIES_DAILY", "symbol": ticker,
                                   "outputsize": "compact"}, key)
    weekly_url, weekly_shown = _url({"function": "TIME_SERIES_WEEKLY", "symbol": ticker}, key)
    try:
        daily = fetch(daily_url)
        if (err := _api_error(daily)) is not None:
            raise err
        sleep(_PAUSE_S)
        weekly = fetch(weekly_url)
        snap = snapshot_from_payloads(ticker, daily, weekly,
                                      fetched_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      urls=(daily_shown, weekly_shown))
    except PriceFetchError as exc:
        raise PriceFetchError(exc.kind, _scrub(str(exc), key)) from None

    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(snap), encoding="utf-8")
    return snap


if __name__ == "__main__":  # python -m financial_agent.prices AAPL: one live fetch, summarized
    import sys

    from dotenv import find_dotenv, load_dotenv

    from financial_agent.indicators import standard_panel

    load_dotenv(find_dotenv(usecwd=True))
    snap = fetch_prices(sys.argv[1] if len(sys.argv) > 1 else "AAPL")
    d, w = snap["series"]["daily"], snap["series"]["weekly"]
    print(f"{snap['ticker']}  source={snap['source']}  as_of={snap['as_of']}  fetched_at={snap['fetched_at']}")
    print(f"daily:  {len(d['rows'])} bars {d['rows'][0][0]} to {d['rows'][-1][0]}  suspect={d['suspect_dates']}")
    print(f"weekly: {len(w['rows'])} bars {w['rows'][0][0]} to {w['rows'][-1][0]}  suspect={w['suspect_dates']}")
    for r in standard_panel(snap):
        print(f"  {r['name']:<13} {json.dumps(r['params']):<38} {r['status']:<20} {json.dumps(r.get('values'))}")
