"""
financial_agent/prices.py against Alpha Vantage's recorded replies, without the network.

What this pins
    - The recorded 2026-10-05 AAPL replies become a snapshot with the right shape: 100 daily
      bars, the weekly series trimmed to WEEKLY_KEEP, as-of the last daily bar, row hashes.
    - Each way Alpha Vantage says no (all inside an HTTP 200, observed live on 2026-10-06)
      becomes an error with its kind: the premium "full" refusal, the rate limit, a bad
      symbol. Never an empty series.
    - The key appears nowhere: not in a recorded URL, not in a snapshot, not in an error.
    - A malformed ticker is refused before any request is built.
    - The day cache: a second fetch the same UTC day makes no request; the next day does.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from financial_agent import prices
from financial_agent.prices import PriceFetchError, fetch_prices, snapshot_from_payloads

FIX = Path(__file__).parent / "fixtures"
DAILY = json.loads((FIX / "av_aapl_daily_compact_2026-10-05.json").read_text(encoding="utf-8"))
WEEKLY = json.loads((FIX / "av_aapl_weekly_2026-10-05_trimmed.json").read_text(encoding="utf-8"))
KEY = "TESTKEY0123456789"
NOW = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)

PREMIUM = {"Information": "Thank you for using Alpha Vantage! The outputsize=full parameter value is a "
                          "premium feature for the TIME_SERIES_DAILY endpoint."}
RATE = {"Information": "Thank you for using Alpha Vantage! Please consider spreading out your free API "
                       "requests more sparingly (1 request per second)."}
BAD = {"Error Message": "Invalid API call. Please retry or visit the documentation for TIME_SERIES_DAILY."}


class Fetcher:
    def __init__(self, daily=DAILY, weekly=WEEKLY):
        self.daily, self.weekly, self.urls = daily, weekly, []

    def __call__(self, url):
        self.urls.append(url)
        return self.daily if "TIME_SERIES_DAILY" in url else self.weekly


def _fetch(fetcher=None, **kw):
    kw.setdefault("cache_dir", None)
    return fetch_prices("AAPL", api_key=KEY, fetch_json=fetcher or Fetcher(), sleep=lambda s: None, now=NOW, **kw)


class TestSnapshot(unittest.TestCase):
    def test_shape_from_the_recorded_replies(self):
        snap = _fetch()
        d, w = snap["series"]["daily"], snap["series"]["weekly"]
        self.assertEqual((snap["ticker"], snap["source"], snap["adjusted"], snap["as_of"]),
                         ("AAPL", "alphavantage", False, "2026-10-05"))
        self.assertEqual(len(d["rows"]), 100)
        self.assertEqual(len(w["rows"]), prices.WEEKLY_KEEP)
        self.assertEqual(d["rows"][-1], ["2026-10-05", 332.815, 336.21, 331.65, 332.89, 34400850])
        self.assertLess(d["rows"][0][0], d["rows"][-1][0])          # oldest first
        self.assertEqual((d["suspect_dates"], w["suspect_dates"]), ([], []))
        self.assertEqual(snap["fetched_at"], "2026-10-06T01:00:00Z")
        self.assertTrue(d["rows_sha256"].startswith("sha256:"))

    def test_both_requests_and_a_pause_between_them(self):
        f, pauses = Fetcher(), []
        fetch_prices("AAPL", api_key=KEY, fetch_json=f, sleep=pauses.append, now=NOW, cache_dir=None)
        self.assertEqual(len(f.urls), 2)
        self.assertIn("outputsize=compact", f.urls[0])
        self.assertIn("TIME_SERIES_WEEKLY", f.urls[1])
        self.assertEqual(pauses, [prices._PAUSE_S])

    def test_impossible_bars_are_refused(self):
        bad = json.loads(json.dumps(DAILY))
        day = max(bad["Time Series (Daily)"])
        bad["Time Series (Daily)"][day]["3. low"] = "999"     # low above high
        with self.assertRaises(PriceFetchError) as cm:
            snapshot_from_payloads("AAPL", bad, WEEKLY, fetched_at="t", urls=("a", "b"))
        self.assertEqual(cm.exception.kind, "bad_response")


class TestAlphaVantageRefusals(unittest.TestCase):
    def test_each_refusal_has_its_kind(self):
        for payload, kind in ((PREMIUM, "premium"), (RATE, "rate_limited"), (BAD, "bad_symbol")):
            with self.subTest(kind=kind):
                with self.assertRaises(PriceFetchError) as cm:
                    _fetch(Fetcher(daily=payload))
                self.assertEqual(cm.exception.kind, kind)

    def test_a_refused_daily_request_stops_before_the_weekly_one(self):
        f = Fetcher(daily=RATE)
        with self.assertRaises(PriceFetchError):
            _fetch(f)
        self.assertEqual(len(f.urls), 1)   # the free key's daily budget isn't spent on a doomed fetch

    def test_a_refused_weekly_request_is_an_error_too(self):
        with self.assertRaises(PriceFetchError) as cm:
            _fetch(Fetcher(weekly=RATE))
        self.assertEqual(cm.exception.kind, "rate_limited")

    def test_no_key(self):
        with self.assertRaises(PriceFetchError) as cm:
            fetch_prices("AAPL", api_key="", fetch_json=Fetcher(), cache_dir=None)
        self.assertEqual(cm.exception.kind, "no_key")

    def test_a_malformed_ticker_never_reaches_a_url(self):
        f = Fetcher()
        for t in ("", "aapl&apikey=x", "TOOLONGTICKER", "A A", "../etc"):
            with self.subTest(t=t), self.assertRaises(PriceFetchError) as cm:
                fetch_prices(t, api_key=KEY, fetch_json=f, cache_dir=None)
            self.assertEqual(cm.exception.kind, "bad_ticker")
        self.assertEqual(f.urls, [])
        self.assertEqual(prices.normalize_ticker(" brk.b "), "BRK.B")


class TestTheKeyIsNeverKept(unittest.TestCase):
    def test_not_in_the_snapshot(self):
        snap = _fetch()
        self.assertNotIn(KEY, json.dumps(snap))
        self.assertTrue(all("apikey=REDACTED" in r["url"] for r in snap["requests"]))

    def test_not_in_an_error(self):
        def leaky(url):
            raise PriceFetchError("network", f"connection reset fetching {url}")
        with self.assertRaises(PriceFetchError) as cm:
            _fetch(leaky)
        self.assertNotIn(KEY, str(cm.exception))
        self.assertIn("REDACTED", str(cm.exception))


class TestDayCache(unittest.TestCase):
    def test_same_day_reuses_next_day_refetches(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Fetcher()
            snap = _fetch(first, cache_dir=Path(tmp))
            again = Fetcher()
            self.assertEqual(_fetch(again, cache_dir=Path(tmp)), snap)
            self.assertEqual(again.urls, [])
            tomorrow = Fetcher()
            fetch_prices("AAPL", api_key=KEY, fetch_json=tomorrow, sleep=lambda s: None,
                         now=datetime(2026, 10, 7, 1, 0, tzinfo=timezone.utc), cache_dir=Path(tmp))
            self.assertEqual(len(tomorrow.urls), 2)
            self.assertNotIn(KEY, "".join(p.read_text(encoding="utf-8") for p in Path(tmp).iterdir()))

    def test_a_failed_fetch_caches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PriceFetchError):
                _fetch(Fetcher(weekly=RATE), cache_dir=Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
