"""
financial_agent/indicators.py: every definition checked against hand-worked values.

Why this file exists
    The agent cites indicator values, and Tier 1 checks those citations against what this
    module computed. If the module is wrong, a correct citation of a wrong number passes. So
    each indicator is pinned on a five-bar series small enough to work through by hand. The
    expected values below are written as the fractions the working gives, not as outputs
    copied from the code.

    The series (date, high, low, close):
        d1 10.5  9.5 10      d4 12.0 10.5 11
        d2 11.5 10.5 11      d5 13.5 12.5 13
        d3 12.5 11.5 12

The refusals are pinned too: too little history and a window crossing a possible split
both return a status, never a value.
"""

from __future__ import annotations

import json
import math
import statistics
import unittest
from pathlib import Path

from financial_agent import indicators as ind
from financial_agent.prices import snapshot_from_payloads, suspect_dates

FIX = Path(__file__).parent / "fixtures"

HIGHS = [10.5, 11.5, 12.5, 12.0, 13.5]
LOWS = [9.5, 10.5, 11.5, 10.5, 12.5]
CLOSES = [10.0, 11.0, 12.0, 11.0, 13.0]


def _snapshot(closes=CLOSES, highs=HIGHS, lows=LOWS, weekly=None):
    rows = [[f"2026-01-{i + 1:02d}", c, h, l, c, 1000] for i, (c, h, l) in enumerate(zip(closes, highs, lows))]
    wrows = weekly if weekly is not None else rows
    return {"series": {"daily": {"rows": rows, "suspect_dates": suspect_dates(rows)},
                       "weekly": {"rows": wrows, "suspect_dates": suspect_dates(wrows)}}}


def _value(name, params=None, snap=None):
    r = ind.compute(snap or _snapshot(), name, params)
    assert r["status"] == "ok", r
    return r["values"]


class TestHandWorked(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(_value("sma", {"period": 3})["value"], 12.0)          # (12 + 11 + 13) / 3

    def test_ema_seeded_with_the_sma_then_k_two_thirds(self):
        # seed (10 + 11)/2 = 10.5; 12 -> 11.5; 11 -> 67/6; 13 -> 223/18
        self.assertEqual(_value("ema", {"period": 2})["value"], round(223 / 18, 4))

    def test_rsi_wilder(self):
        # changes +1 +1 -1 +2. avg gain/loss: 1/0, then 1/2 and 1/2, then 5/4 and 1/4. RS = 5.
        self.assertEqual(_value("rsi", {"period": 2})["value"], round(100 - 100 / 6, 4))

    def test_rsi_edges(self):
        # (Steps small enough not to look like a split: 1 -> 2 would be flagged.)
        up = _snapshot(closes=[10, 11, 12, 13], highs=[10, 11, 12, 13], lows=[10, 11, 12, 13])
        down = _snapshot(closes=[13, 12, 11, 10], highs=[13, 12, 11, 10], lows=[13, 12, 11, 10])
        flat = _snapshot(closes=[2, 2, 2, 2], highs=[2, 2, 2, 2], lows=[2, 2, 2, 2])
        self.assertEqual(_value("rsi", {"period": 2}, up)["value"], 100.0)
        self.assertEqual(_value("rsi", {"period": 2}, down)["value"], 0.0)
        self.assertEqual(_value("rsi", {"period": 2}, flat)["value"], 50.0)

    def test_macd(self):
        # ema2 from d2: 21/2, 23/2, 67/6, 223/18. ema3 from d3: 11, 11, 12.
        # line 1/2, 1/6, 7/18; signal (ema2 of the line) 1/3 then 10/27; histogram 1/54.
        v = _value("macd", {"fast": 2, "slow": 3, "signal": 2})
        self.assertEqual(v, {"macd": round(7 / 18, 4), "signal": round(10 / 27, 4), "histogram": round(1 / 54, 4)})

    def test_atr_wilder(self):
        # true ranges from d2: 1.5, 1.5, 1.5, 2.5. ATR: 1.5, 1.5, then (1.5 + 2.5)/2 = 2.
        self.assertEqual(_value("atr", {"period": 2})["value"], 2.0)

    def test_return(self):
        self.assertEqual(_value("return", {"bars": 2})["value"], round((13 / 12 - 1) * 100, 4))

    def test_max_drawdown(self):
        # peak 12 at d3, then 11: -1/12
        self.assertEqual(_value("max_drawdown", {"bars": 5})["value"], round(-100 / 12, 4))

    def test_range(self):
        v = _value("range", {"bars": 3})
        self.assertEqual(v, {"high": 13.5, "low": 10.5, "pct_from_high": round((13 / 13.5 - 1) * 100, 4),
                             "pct_from_low": round((13 / 10.5 - 1) * 100, 4)})

    def test_volatility_sample_stdev_of_log_returns_annualized(self):
        rets = [math.log(11 / 12), math.log(13 / 11)]
        expected = statistics.stdev(rets) * math.sqrt(252) * 100
        self.assertEqual(_value("volatility", {"window": 2})["value"], round(expected, 4))
        weekly = _value("volatility", {"window": 2, "series": "weekly"})["value"]
        self.assertEqual(weekly, round(statistics.stdev(rets) * math.sqrt(52) * 100, 4))

    def test_last_close(self):
        self.assertEqual(_value("last_close"), {"close": 13.0, "date": "2026-01-05"})


class TestRefusals(unittest.TestCase):
    def test_too_little_history_is_a_status_not_a_shorter_window(self):
        r = ind.compute(_snapshot(), "sma", {"period": 6})
        self.assertEqual((r["status"], r["needed"], r["have"]), ("insufficient_history", 6, 5))
        self.assertNotIn("values", r)
        self.assertEqual(ind.compute(_snapshot(), "rsi", {"period": 5})["status"], "insufficient_history")
        self.assertEqual(ind.compute(_snapshot(), "macd", {"fast": 2, "slow": 4, "signal": 3})["status"],
                         "insufficient_history")   # needs 4 + 3 - 1 = 6

    def test_a_window_across_a_possible_split_refuses(self):
        split = _snapshot(closes=[100, 101, 25.5, 26], highs=[101, 102, 26, 27], lows=[99, 100, 25, 25.5])
        self.assertEqual(split["series"]["daily"]["suspect_dates"], ["2026-01-03"])
        r = ind.compute(split, "sma", {"period": 3})
        self.assertEqual((r["status"], r["split_date"]), ("possible_split", "2026-01-03"))
        # Two bars, both after the split: nothing crossed.
        self.assertEqual(ind.compute(split, "sma", {"period": 2})["values"]["value"], 25.75)
        # The EMA family uses the whole series, so any split in it counts.
        self.assertEqual(ind.compute(split, "ema", {"period": 2})["status"], "possible_split")

    def test_malformed_requests_raise(self):
        for name, params in (("nope", {}), ("sma", {"period": 0}), ("sma", {"period": "20"}),
                             ("sma", {"period": True}), ("sma", {"window": 3}),
                             ("sma", {"series": "monthly"}), ("macd", {"fast": 26, "slow": 12})):
            with self.subTest(name=name, params=params), self.assertRaises(ind.IndicatorError):
                ind.compute(_snapshot(), name, params)


class TestOnTheRealAaplSnapshot(unittest.TestCase):
    """The recorded 2026-10-05 Alpha Vantage replies (tests/fixtures/av_aapl_*)."""

    @classmethod
    def setUpClass(cls):
        daily = json.loads((FIX / "av_aapl_daily_compact_2026-10-05.json").read_text(encoding="utf-8"))
        weekly = json.loads((FIX / "av_aapl_weekly_2026-10-05_trimmed.json").read_text(encoding="utf-8"))
        cls.snap = snapshot_from_payloads("AAPL", daily, weekly, fetched_at="2026-10-06T00:00:00Z", urls=("d", "w"))

    def test_every_standard_indicator_has_a_value(self):
        panel = ind.standard_panel(self.snap)
        self.assertEqual(len(panel), len(ind.STANDARD_PANEL))
        self.assertEqual({r["status"] for r in panel}, {"ok"})
        self.assertEqual({r["as_of"] for r in panel if r["series"] == "daily"}, {"2026-10-05"})

    def test_a_200_day_average_is_refused_on_100_daily_bars(self):
        r = ind.compute(self.snap, "sma", {"period": 200})
        self.assertEqual((r["status"], r["have"]), ("insufficient_history", 100))

    def test_simple_values_match_the_raw_reply(self):
        closes = [r[4] for r in self.snap["series"]["daily"]["rows"]]
        self.assertEqual(_value("last_close", snap=self.snap)["close"], 332.89)
        self.assertEqual(_value("sma", {"period": 20}, self.snap)["value"], round(sum(closes[-20:]) / 20, 4))

    def test_describe_reads_cleanly(self):
        r = ind.compute(self.snap, "rsi", {"period": 14})
        self.assertRegex(ind.describe(r), r"^rsi\(period=14, daily\) = [\d.]+ index$")
        r = ind.compute(self.snap, "sma", {"period": 200})
        self.assertEqual(ind.describe(r), "sma(period=200, daily): insufficient_history (needs 200 bars, have 100)")


if __name__ == "__main__":
    unittest.main()
