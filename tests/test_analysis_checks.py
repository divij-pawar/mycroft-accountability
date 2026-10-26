"""
Tier 1 grounding of an analysis (validation/analysis_checks.py, financial_agent/PLAN.md 6.3).

Every check here runs on tool results produced by the real tools over the recorded AAPL bundle
(no hand-typed results), so a change to what a tool returns shows up as a changed check.
Network-free and model-free. This tests whether figures were quoted correctly; whether the
signal is right is not something it can say.
"""

from __future__ import annotations

import unittest

from financial_agent.tools import ToolRecorder
from tests.test_fa_agent import SIGNAL, _bundle
from validation import analysis_checks as T
from validation.analysis_checks import (
    GROUNDED, JUDGMENT, MISMATCH, UNREFERENCED, USER_ASSERTED, check_analysis, check_figure, matches, numbers_in,
)


def recorded():
    """A record whose calls are 1 describe_data, 2 get_indicators, 3 get_filing_facts, 4 get_prices (3 bars)."""
    rec = ToolRecorder(_bundle())
    rec.call("a", "describe_data")
    rec.call("a", "get_indicators")
    rec.call("a", "get_filing_facts")
    rec.call("a", "get_prices", {"last": 3})
    return {"tool_calls": rec.calls, "output": {"key_points": [], "cited_figures": []}}


def fig(name, value, call=2, unit=None):
    out = {"name": name, "value": value, "ref": {"call": call}}
    if unit:
        out["unit"] = unit
    return out


class TestMatches(unittest.TestCase):
    """At the precision the figure was written; a stated unit scales it, no unit means no scaling."""

    def test_the_precision_it_was_written_at(self):
        self.assertTrue(matches(53.8, None, 53.7839))        # one decimal: half a tenth either way
        self.assertTrue(matches(53.78, None, 53.7839))
        self.assertTrue(matches(53.7839, None, 53.7839))
        self.assertFalse(matches(53.7, None, 53.7839))
        self.assertFalse(matches(58.2, None, 53.7839))

    def test_an_integer_is_exact(self):
        self.assertTrue(matches(34400850, None, 34400850))
        self.assertFalse(matches(34400851, None, 34400850))

    def test_a_stated_unit_scales_the_figure(self):
        self.assertTrue(matches(109.417, "USD billion", 109417000000.0))
        self.assertTrue(matches(109.417, "billions", 109417000000.0))
        self.assertTrue(matches(109417, "million", 109417000000.0))
        self.assertTrue(matches(109.417, "$B", 109417000000.0))
        self.assertTrue(matches(109.4, "billion", 109417000000.0))

    def test_a_unit_slip_does_not_match(self):
        self.assertFalse(matches(109.417, "million", 109417000000.0))
        self.assertFalse(matches(109.417, None, 109417000000.0))
        self.assertFalse(matches(109417, "billion", 109417000000.0))

    def test_units_that_are_not_scales_leave_the_figure_alone(self):
        for unit in ("USD", "index", "USD/share", "pct_annualized", "percent", "shares"):
            self.assertTrue(matches(2.02, unit, 2.02), unit)
            self.assertFalse(matches(2.02, unit, 2020000.0), unit)

    def test_a_negative_figure_keeps_its_sign(self):
        self.assertTrue(matches(-1.06, None, -1.061))
        self.assertFalse(matches(1.06, None, -1.061))


class TestFigureStates(unittest.TestCase):

    def setUp(self):
        self.record = recorded()

    def check(self, *a, **kw):
        return check_figure(fig(*a, **kw), self.record)

    def test_a_figure_that_matches_the_call_it_cites_is_grounded(self):
        r = self.check("rsi_14", 53.78, unit="index")
        self.assertEqual(r["status"], GROUNDED)
        self.assertEqual(r["matched_by"], "name")
        self.assertIn("rsi", r["matched"]["path"])

    def test_loose_names_for_the_right_number_are_accepted(self):
        for name, value, call in (("RSI", 53.7839, 2), ("sma_20", 332.54, 2), ("macd_signal", 4.53, 2),
                                  ("close", 332.89, 4), ("volume", 34400850, 4), ("diluted_eps", 2.02, 3)):
            self.assertEqual(self.check(name, value, call)["status"], GROUNDED, name)

    def test_a_figure_that_contradicts_the_call_is_a_mismatch_that_says_what_the_call_has(self):
        r = self.check("rsi_14", 58.2)
        self.assertEqual(r["status"], MISMATCH)
        self.assertIn("53.7839", r["reason"])
        self.assertTrue(any(abs(v["value"] - 53.7839) < 1e-6 for v in r["call_values"]))

    def test_a_filing_figure_with_a_unit_slip_is_a_mismatch(self):
        self.assertEqual(self.check("revenue", 109.417, 3, "billion")["status"], GROUNDED)
        self.assertEqual(self.check("revenue", 109.417, 3, "million")["status"], MISMATCH)
        self.assertEqual(self.check("revenue", 109.417, 3)["status"], MISMATCH)

    def test_a_filing_figure_off_by_one_in_the_last_digit_is_a_mismatch(self):
        # Net income is 29,789 million; 29.788 is a transposition-sized slip at three decimals.
        self.assertEqual(self.check("net_income", 29.789, 3, "billion")["status"], GROUNDED)
        self.assertEqual(self.check("net_income", 29.788, 3, "billion")["status"], MISMATCH)

    def test_fiscal_year_and_day_counts_are_not_numbers_a_figure_can_mean(self):
        r = self.check("revenue", 2026, 3)
        self.assertNotEqual(r["status"], GROUNDED)
        values = {v["value"] for v in r.get("call_values", [])}
        self.assertNotIn(2026.0, values)
        self.assertNotIn(90.0, values)

    def test_a_parameter_the_data_lacks_is_a_mismatch_even_if_the_value_is_a_neighbours(self):
        sma_50 = 322.4136
        self.assertEqual(self.check("sma_50", sma_50)["status"], GROUNDED)
        r = self.check("sma_21", sma_50)
        self.assertEqual(r["status"], MISMATCH)
        self.assertIn("21", r["reason"])
        self.assertIn("20", r["reason"])

    def test_a_price_named_for_a_date_needs_that_days_bar(self):
        self.assertEqual(self.check("close_2026-10-05", 332.89, 4)["status"], GROUNDED)
        self.assertEqual(self.check("close_2026-10-02", 333.69, 4)["status"], GROUNDED)
        self.assertEqual(self.check("close_2026-10-05", 333.69, 4)["status"], MISMATCH)
        r = self.check("close_2026-10-03", 333.69, 4)
        self.assertEqual(r["status"], MISMATCH)
        self.assertIn("no bar for 2026-10-03", r["reason"])

    def test_a_name_that_points_at_nothing_but_a_value_that_matches_is_grounded_by_value_and_says_so(self):
        r = self.check("momentum_score", 53.7839)
        self.assertEqual(r["status"], GROUNDED)
        self.assertEqual(r["matched_by"], "value")
        self.assertIn("name matched nothing", r["reason"])

    def test_a_figure_that_matches_nothing_in_its_call_is_unreferenced(self):
        r = self.check("whatever", 5, 1)
        self.assertEqual(r["status"], UNREFERENCED)
        self.assertIn("describe_data", r["reason"])

    def test_a_call_that_does_not_exist_is_unreferenced(self):
        r = self.check("rsi_14", 53.78, 9)
        self.assertEqual(r["status"], UNREFERENCED)
        self.assertIn("no call 9", r["reason"])

    def test_a_call_that_returned_an_error_grounds_nothing(self):
        rec = ToolRecorder(_bundle())
        rec.call("a", "get_prices", {"last": 99999})
        r = check_figure(fig("close", 332.89, 1), {"tool_calls": rec.calls})
        self.assertEqual(r["status"], UNREFERENCED)
        self.assertIn("returned an error", r["reason"])

    def test_a_document_figure_is_user_asserted_never_grounded(self):
        r = check_figure({"name": "guidance", "value": 5, "ref": {"chunk": "d1:2"}}, self.record)
        self.assertEqual(r["status"], USER_ASSERTED)
        self.assertIn("never checked", r["reason"])

    def test_a_judgment_is_neither_grounded_nor_not(self):
        r = check_figure({"name": "mood", "value": 1, "ref": {"judgment": True}}, self.record)
        self.assertEqual(r["status"], JUDGMENT)


class TestNumbersIn(unittest.TestCase):

    def test_every_number_comes_with_the_words_that_say_what_it_is(self):
        items = numbers_in(recorded()["tool_calls"][1]["result"])
        rsi = [i for i in items if "rsi" in i["words"] and i["path"].endswith(".value")]
        self.assertTrue(rsi)
        self.assertTrue(any({"rsi", "14"} <= i["words"] for i in rsi))

    def test_bars_used_and_other_metadata_are_not_numbers_a_figure_can_mean(self):
        items = numbers_in(recorded()["tool_calls"][1]["result"])
        self.assertFalse([i for i in items if i["path"].endswith("bars_used")])

    def test_strings_dates_and_booleans_are_not_numbers(self):
        self.assertEqual(numbers_in({"a": "5", "b": True, "c": None, "d": [], "e": {"f": "x"}}), [])


class TestAnalysis(unittest.TestCase):

    def record_with(self, figures, key_points=None):
        rec = recorded()
        rec["output"] = {"signal": "buy", "key_points": key_points or [], "cited_figures": figures}
        return rec

    def test_the_summary_counts_what_can_be_checked(self):
        rec = self.record_with([
            fig("rsi_14", 53.78),                                                # grounded
            fig("sma_20", 332.54),                                               # grounded
            fig("macd_signal", 9.99),                                            # mismatch
            fig("whatever", 5, 1),                                               # unreferenced
            {"name": "guidance", "value": 5, "ref": {"chunk": "d:1"}},           # user asserted
            {"name": "mood", "value": 1, "ref": {"judgment": True}},             # judgment
        ])
        out = check_analysis(rec)
        self.assertEqual((out["grounded"], out["total"], out["mismatched"], out["unreferenced"]), (2, 4, 1, 1))
        self.assertEqual((out["user_asserted"], out["judgment"]), (1, 1))
        self.assertEqual(out["version"], T.CHECKS_VERSION)
        self.assertEqual([c["status"] for c in out["checks"]],
                         [GROUNDED, GROUNDED, MISMATCH, UNREFERENCED, USER_ASSERTED, JUDGMENT])

    def test_no_figures_is_zero_of_zero_not_a_pass(self):
        out = check_analysis(self.record_with([]))
        self.assertEqual((out["grounded"], out["total"]), (0, 0))

    def test_a_halted_run_has_nothing_to_check(self):
        self.assertIsNone(check_analysis({"tool_calls": [], "output": None}))

    def test_a_key_point_that_cites_a_missing_or_failed_call_is_listed(self):
        rec = ToolRecorder(_bundle())
        rec.call("a", "get_prices", {"last": 99999})        # call 1 is an error
        rec.call("a", "describe_data")                      # call 2 is fine
        record = {"tool_calls": rec.calls, "output": {"signal": "buy", "cited_figures": [], "key_points": [
            {"text": "a", "evidence": [{"call": 1}]}, {"text": "b", "evidence": [{"call": 2}, {"call": 7}]},
            {"text": "c", "evidence": [{"judgment": True}]}]}}
        problems = check_analysis(record)["evidence_problems"]
        self.assertEqual([(p["key_point"], p["ref"]) for p in problems], [(1, {"call": 1}), (2, {"call": 7})])

    def test_a_real_agent_shaped_signal_with_one_misquoted_figure(self):
        # tests.test_fa_agent.SIGNAL quotes RSI as 58.2; call 2's panel says 53.78.
        rec = recorded()
        rec["output"] = SIGNAL
        out = check_analysis(rec)
        self.assertEqual((out["grounded"], out["mismatched"]), (0, 1))
        self.assertIn("53.7839", out["checks"][0]["reason"])

    def test_the_result_is_plain_json(self):
        import json
        out = check_analysis(self.record_with([fig("rsi_14", 53.78)]))
        self.assertEqual(json.loads(json.dumps(out)), out)

    def test_the_checker_does_not_change_the_record(self):
        import copy
        rec = self.record_with([fig("rsi_14", 58.2)])
        before = copy.deepcopy(rec)
        check_analysis(rec)
        self.assertEqual(rec, before)


if __name__ == "__main__":
    unittest.main()
