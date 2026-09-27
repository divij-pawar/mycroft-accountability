"""
Earnings grader tests — Producer B, real-source counterpart to test_financial_grader.py.
No network: fetch_fn/call_agent_fn are always injected fakes.
LangFuse tracing itself is not asserted on here (the SDK degrades gracefully without
configured credentials — see pipeline/observability.py); this only verifies the
accountability-mesh behavior is unchanged for the earnings concept slice.
"""

import unittest
import uuid

from tests.support import make_scripted_adapter
from datasources.edgar import EdgarFetchError, lookup_cik
from producers.earnings import analyze_earnings, summarize_earnings_facts
from pipeline.middleware import HaltError, ValidationLoopResult
from core.schemas import AgentID, ParseStatus

_TICKER_MAP = {
    "0000320193": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
}

_FACTS = {
    "facts": {
        "us-gaap": {
            "EarningsPerShareDiluted": {
                "units": {
                    "USD/shares": [
                        {"end": "2025-09-30", "val": 6.10},
                        {"end": "2026-03-31", "val": 6.45},
                    ]
                }
            },
            "OperatingIncomeLoss": {
                "units": {
                    "USD": [
                        {"end": "2026-03-31", "val": 120000000000},
                    ]
                }
            },
            # EarningsPerShareBasic deliberately absent to test the "not reported" path
        }
    }
}


def _fake_ticker_map_fetch(url: str) -> dict:
    return _TICKER_MAP


def _fake_facts_fetch(url: str) -> dict:
    return _FACTS


class TestSummarizeEarningsFacts(unittest.TestCase):

    def test_includes_ticker(self):
        summary = summarize_earnings_facts("AAPL", _FACTS)
        self.assertIn("Ticker: AAPL", summary)

    def test_picks_latest_diluted_eps_value(self):
        summary = summarize_earnings_facts("AAPL", _FACTS)
        self.assertIn("EarningsPerShareDiluted: 6.45", summary)

    def test_missing_concept_reported_as_not_reported(self):
        summary = summarize_earnings_facts("AAPL", _FACTS)
        self.assertIn("EarningsPerShareBasic: not reported", summary)

    def test_empty_facts_all_not_reported(self):
        summary = summarize_earnings_facts("AAPL", {"facts": {}})
        self.assertIn("EarningsPerShareDiluted: not reported", summary)
        self.assertIn("OperatingIncomeLoss: not reported", summary)

    def test_different_concepts_than_financial_grader(self):
        # The whole point of a second producer: different evidence, same company.
        summary = summarize_earnings_facts("AAPL", _FACTS)
        self.assertNotIn("Assets:", summary)
        self.assertNotIn("Revenues:", summary)


class TestAnalyzeEarnings(unittest.TestCase):

    def setUp(self):
        self.run_id = uuid.uuid4()

    def test_happy_path_returns_validation_loop_result(self):
        result = analyze_earnings(
            "AAPL",
            "0000320193",
            make_scripted_adapter("none"),
            run_id=self.run_id,
            fetch_fn=_fake_facts_fetch,
        )
        self.assertIsInstance(result, ValidationLoopResult)

    def test_happy_path_reasoning_object_for_earnings_agent(self):
        result = analyze_earnings(
            "AAPL",
            "0000320193",
            make_scripted_adapter("none"),
            run_id=self.run_id,
            fetch_fn=_fake_facts_fetch,
        )
        obj = result.reasoning_objects[0]
        self.assertEqual(obj.agent_id, AgentID.EARNINGS)
        self.assertEqual(obj.parse_status, ParseStatus.SUCCESS)
        self.assertEqual(obj.run_id, self.run_id)

    def test_context_passed_to_agent_includes_earnings_facts(self):
        # mock_adapter echoes the context into thought_log — confirms the earnings
        # concept slice actually flowed into the LLM call, not just a placeholder.
        result = analyze_earnings(
            "AAPL",
            "0000320193",
            make_scripted_adapter("none"),
            run_id=self.run_id,
            fetch_fn=_fake_facts_fetch,
        )
        self.assertIn("EarningsPerShareDiluted: 6.45", result.reasoning_objects[0].thought_log)

    def test_retry_success_still_reaches_success(self):
        result = analyze_earnings(
            "AAPL",
            "0000320193",
            make_scripted_adapter("retry_success"),
            run_id=self.run_id,
            fetch_fn=_fake_facts_fetch,
        )
        self.assertEqual(len(result.reasoning_objects), 2)
        self.assertEqual(result.reasoning_objects[1].parse_status, ParseStatus.SUCCESS)

    def test_halt_mode_raises_halt_error(self):
        with self.assertRaises(HaltError) as ctx:
            analyze_earnings(
                "AAPL",
                "0000320193",
                make_scripted_adapter("halt"),
                run_id=self.run_id,
                fetch_fn=_fake_facts_fetch,
            )
        self.assertEqual(len(ctx.exception.reasoning_objects), 2)

    def test_bad_edgar_fetch_propagates(self):
        def _broken_fetch(url: str) -> dict:
            raise EdgarFetchError("simulated network failure")

        with self.assertRaises(EdgarFetchError):
            analyze_earnings(
                "AAPL",
                "0000320193",
                make_scripted_adapter("none"),
                run_id=self.run_id,
                fetch_fn=_broken_fetch,
            )

    def test_reuses_financial_grader_lookup_cik(self):
        # Both producers share one CIK-resolution path — there is no separate
        # "earnings" ticker map, because it's the same company, same SEC filer.
        cik = lookup_cik("AAPL", fetch_fn=_fake_ticker_map_fetch)
        self.assertEqual(cik, "0000320193")


if __name__ == "__main__":
    unittest.main()
