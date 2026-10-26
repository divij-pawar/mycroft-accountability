"""
The frozen-input chain: bundle -> tools -> recorded calls -> replay (financial_agent/).

What this pins (PLAN.md D6, 4.5)
    - A bundle's hash covers its data only, and is independent of key order and of when it
      was built: the same data is the same bundle.
    - Changing any stored price, or the rows under their recorded hash, is detected.
    - The tools answer only from the bundle. A request it can't answer is an {"error": ...}
      result, recorded like any other, never an invented value.
    - Calls are numbered 1, 2, 3 ... with no gaps or duplicates, even from two threads at once.
    - Replay refuses a damaged bundle, and finds a recorded result that the data no longer
      gives, or that doesn't match its own hash.
    - The filing snapshot keeps each fact's period metadata and lists what the filer doesn't report.
"""

from __future__ import annotations

import copy
import json
import threading
import unittest
from pathlib import Path

from core.analysis import content_hash
from financial_agent.bundle import BundleError, build_bundle, bundle_problems, filing_snapshot
from financial_agent.prices import snapshot_from_payloads
from financial_agent.replay import ReplayError, check_recorded_calls, load_bundle, replay_recorder
from financial_agent.tools import TOOLS, ToolRecorder, run_tool

FIX = Path(__file__).parent / "fixtures"


def _prices():
    daily = json.loads((FIX / "av_aapl_daily_compact_2026-10-05.json").read_text(encoding="utf-8"))
    weekly = json.loads((FIX / "av_aapl_weekly_2026-10-05_trimmed.json").read_text(encoding="utf-8"))
    return snapshot_from_payloads("AAPL", daily, weekly, fetched_at="2026-10-06T01:00:00Z", urls=("d", "w"))


def _filing():
    facts = json.loads((FIX / "edgar_aapl_companyfacts_sample.json").read_text(encoding="utf-8"))
    return filing_snapshot("AAPL", facts, "0000320193")


def _bundle(**kw):
    return build_bundle(ticker="AAPL", prices=_prices(), filing=_filing(), created_at="2026-10-06T01:00:00Z", **kw)


class TestBundle(unittest.TestCase):
    def test_hash_covers_data_only(self):
        a = _bundle()
        b = build_bundle(ticker="AAPL", prices=_prices(), filing=_filing(), created_at="2027-01-01T00:00:00Z")
        self.assertEqual(a["bundle_hash"], b["bundle_hash"])
        reordered = json.loads(json.dumps(a["data"]), object_pairs_hook=lambda kv: dict(reversed(kv)))
        self.assertEqual(content_hash(reordered), a["bundle_hash"])
        self.assertEqual(bundle_problems(a), [])
        self.assertEqual(a["data"]["as_of"], "2026-10-05")

    def test_a_changed_price_is_detected(self):
        b = _bundle()
        b["data"]["prices"]["series"]["daily"]["rows"][-1][4] = 333.0
        problems = bundle_problems(b)
        self.assertTrue(any("hash mismatch" in p for p in problems))
        self.assertTrue(any("daily price rows" in p for p in problems))

    def test_mismatched_tickers_are_refused(self):
        with self.assertRaises(BundleError):
            build_bundle(ticker="MSFT", prices=_prices())
        with self.assertRaises(BundleError):
            build_bundle(ticker="AAPL", prices=_prices(), filing={**_filing(), "ticker": "MSFT"})

    def test_nan_cannot_enter_a_bundle(self):
        p = _prices()
        p["series"]["daily"]["rows"][0][4] = float("nan")
        with self.assertRaises(ValueError):
            build_bundle(ticker="AAPL", prices=p)

    def test_filing_snapshot_keeps_period_metadata_and_lists_gaps(self):
        f = _filing()
        by = {x["concept"]: x for x in f["facts"]}
        self.assertEqual(f["entity"], "Apple Inc.")
        self.assertEqual(by["EarningsPerShareDiluted"]["period_kind"], "quarter")   # not the 9-month YTD
        self.assertIn("accn", by["NetIncomeLoss"])
        self.assertIn("GrossProfit", f["missing"])   # not in the trimmed fixture
        self.assertEqual(f["stale"], [])

    def test_a_fact_older_than_the_latest_period_is_marked_stale(self):
        # The shape seen live on 2026-10-06: cash flow only year-to-date after Q1, so the one
        # standalone quarter is two quarters older than the rest of the 10-Q.
        def entry(val, start, end, fp):
            return {"val": val, "start": start, "end": end, "fy": 2026, "fp": fp, "form": "10-Q",
                    "filed": end, "accn": "0000320193-26-000001"}
        facts = {"cik": 320193, "entityName": "Apple Inc.", "facts": {"us-gaap": {
            "NetIncomeLoss": {"units": {"USD": [entry(29.789e9, "2026-03-29", "2026-06-27", "Q3")]}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                entry(53.925e9, "2025-09-28", "2025-12-27", "Q1"),
                entry(110.0e9, "2025-09-28", "2026-06-27", "Q3")]}},   # nine months, year-to-date
        }}}
        f = filing_snapshot("AAPL", facts)
        by = {x["concept"]: x for x in f["facts"]}
        self.assertEqual(f["latest_period_end"], "2026-06-27")
        self.assertEqual(f["stale"], ["NetCashProvidedByUsedInOperatingActivities"])
        self.assertTrue(by["NetCashProvidedByUsedInOperatingActivities"]["stale"])
        self.assertEqual(by["NetCashProvidedByUsedInOperatingActivities"]["end"], "2025-12-27")
        self.assertFalse(by["NetIncomeLoss"]["stale"])
        data = build_bundle(ticker="AAPL", prices=_prices(), filing=f)["data"]
        self.assertEqual(run_tool(data, "describe_data", {})["filing"]["older_than_latest_period"],
                         ["NetCashProvidedByUsedInOperatingActivities"])


class TestTools(unittest.TestCase):
    def setUp(self):
        self.data = _bundle()["data"]

    def test_describe_data(self):
        d = run_tool(self.data, "describe_data", {})
        self.assertEqual((d["ticker"], d["as_of"], d["series"]["daily"]["bars"]), ("AAPL", "2026-10-05", 100))
        self.assertIn("NetIncomeLoss", d["filing"]["concepts"])

    def test_get_prices_returns_the_latest_bars(self):
        r = run_tool(self.data, "get_prices", {"last": 5})
        self.assertEqual(len(r["rows"]), 5)
        self.assertEqual(r["rows"][-1][0], "2026-10-05")
        self.assertEqual(len(run_tool(self.data, "get_prices", {"series": "weekly", "last": 260})["rows"]), 156)

    def test_get_indicators_default_panel_and_requests(self):
        panel = run_tool(self.data, "get_indicators", {})["results"]
        self.assertTrue(len(panel) > 10)
        r = run_tool(self.data, "get_indicators", {"requests": [{"name": "rsi", "params": {"period": 14}},
                                                                {"name": "sma", "params": {"period": 200}}]})
        self.assertEqual([x["status"] for x in r["results"]], ["ok", "insufficient_history"])

    def test_get_filing_facts_filters(self):
        r = run_tool(self.data, "get_filing_facts", {"concepts": ["NetIncomeLoss"]})
        self.assertEqual([f["concept"] for f in r["facts"]], ["NetIncomeLoss"])

    def test_bad_requests_are_error_results_not_exceptions(self):
        cases = [("get_prices", {"last": 0}), ("get_prices", {"series": "hourly"}), ("get_prices", {"limit": 3}),
                 ("get_indicators", {"requests": [{"name": "nope"}]}), ("get_indicators", {"requests": []}),
                 ("fetch_url", {"url": "http://x"}), ("get_prices", "not an object")]
        for tool, args in cases:
            with self.subTest(tool=tool, args=args):
                self.assertIn("error", run_tool(self.data, tool, args))

    def test_no_filing_data_is_said_so(self):
        data = build_bundle(ticker="AAPL", prices=_prices())["data"]
        self.assertEqual(run_tool(data, "get_filing_facts", {}), {"error": "this analysis has no SEC filing data"})
        self.assertEqual(run_tool(data, "describe_data", {})["filing"], "no SEC filing data in this analysis")

    def test_every_tool_has_a_spec(self):
        self.assertEqual(set(TOOLS), {"describe_data", "get_prices", "get_indicators", "get_filing_facts"})


class TestRecorder(unittest.TestCase):
    def test_calls_are_numbered_and_errors_recorded(self):
        rec = ToolRecorder(_bundle())
        rec.call("a", "describe_data")
        rec.call("a", "get_prices", {"last": 0})
        rec.call("b", "get_indicators")
        self.assertEqual([c["seq"] for c in rec.calls], [1, 2, 3])
        self.assertIn("error", rec.calls[1]["result"])
        self.assertEqual([c["seq"] for c in rec.calls_for("b")], [3])
        for c in rec.calls:
            self.assertEqual(c["result_sha256"], content_hash(c["result"]))

    def test_two_threads_never_share_or_skip_a_number(self):
        rec = ToolRecorder(_bundle())

        def work(slot):
            for _ in range(40):
                rec.call(slot, "get_prices", {"last": 3})

        threads = [threading.Thread(target=work, args=(s,)) for s in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([c["seq"] for c in rec.calls], list(range(1, 81)))


class TestReplay(unittest.TestCase):
    def setUp(self):
        self.bundle = _bundle()
        rec = ToolRecorder(self.bundle)
        rec.call("a", "get_indicators")
        rec.call("a", "get_prices", {"last": 10})
        rec.call("a", "get_filing_facts", {})
        self.calls = json.loads(json.dumps(rec.calls))      # as stored and read back

    def test_recorded_calls_reproduce_from_a_stored_bundle(self):
        stored = json.loads(json.dumps(self.bundle))
        self.assertEqual(check_recorded_calls(stored, self.calls), [])

    def test_an_edited_result_is_found(self):
        calls = copy.deepcopy(self.calls)
        calls[1]["result"]["rows"][-1][4] = 999.0
        calls[1]["result_sha256"] = content_hash(calls[1]["result"])    # even with a matching hash
        problems = check_recorded_calls(self.bundle, calls)
        self.assertEqual([(p["seq"], p["problem"]) for p in problems],
                         [(2, "the bundle gives a different result for this call")])

    def test_a_result_that_doesnt_match_its_hash_is_found(self):
        calls = copy.deepcopy(self.calls)
        calls[0]["result"]["as_of"] = "2020-01-01"
        self.assertEqual(check_recorded_calls(self.bundle, calls)[0]["problem"],
                         "recorded result doesn't match its own hash")

    def test_a_gap_in_the_numbering_is_found(self):
        calls = [self.calls[0], self.calls[2]]
        self.assertEqual(check_recorded_calls(self.bundle, calls)[0]["problem"], "expected seq 2")

    def test_a_damaged_bundle_is_refused(self):
        damaged = copy.deepcopy(self.bundle)
        damaged["data"]["as_of"] = "2026-10-06"
        with self.assertRaises(ReplayError):
            load_bundle(damaged)
        with self.assertRaises(ReplayError):
            replay_recorder(damaged)

    def test_agent_b_asks_its_own_questions_of_the_same_data(self):
        b = replay_recorder(json.loads(json.dumps(self.bundle)))
        r = b.call("b", "get_indicators", {"requests": [{"name": "ema", "params": {"period": 50}}]})
        self.assertEqual(r["results"][0]["status"], "ok")
        self.assertEqual(r["as_of"], "2026-10-05")
        self.assertEqual(b.calls[0]["seq"], 1)


if __name__ == "__main__":
    unittest.main()
