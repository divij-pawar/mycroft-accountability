"""
Period- and unit-aware fact selection (datasources/edgar.py), against a real payload.

Why this file exists
    Until B0, latest_value() picked `max(end)` across every entry of a concept and
    returned a bare float. Run against AAPL's real companyfacts payload (fixture
    below, trimmed from a live fetch on 2026-09-24), that rule handed the agents:

      * Revenues = $265.6B — FY2018 annual revenue. Apple stopped using the
        us-gaap:Revenues tag after FY2018; current revenue is reported under
        RevenueFromContractWithCustomerExcludingAssessedTax.
      * EarningsPerShareDiluted = 6.88 — the nine-month year-to-date figure. The
        Q3 quarter itself was 2.02. The 10-Q tags both with the same `end` date, and
        max() returned whichever came first in the list.
      * NetIncomeLoss / OperatingIncomeLoss — the same year-to-date-vs-quarter mix-up.

    None of it was labeled, so no agent (or reviewer) could tell.
    TestLegacyRuleOnRealData pins that old behaviour so the record of the bug
    doesn't depend on anyone's memory; the rest pins the fix.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from datasources.edgar import Fact, latest_value, select_fact
from producers.earnings import summarize_earnings_facts
from producers.financial import summarize_facts

_FIXTURE = Path(__file__).parent / "fixtures" / "edgar_aapl_companyfacts_sample.json"
AAPL = json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _legacy_latest_entry(facts: dict, concept: str) -> dict:
    """The pre-B0 rule, reproduced verbatim: max(end) across every unit bucket."""
    units = facts["facts"]["us-gaap"][concept]["units"]
    entries = [e for es in units.values() for e in es if isinstance(e.get("val"), (int, float))]
    return max(entries, key=lambda e: e.get("end", ""))


def _days(entry: dict) -> int:
    return (date.fromisoformat(entry["end"]) - date.fromisoformat(entry["start"])).days


class TestLegacyRuleOnRealData(unittest.TestCase):
    """What the agents were actually being told before B0."""

    def test_legacy_rule_picked_the_nine_month_ytd_eps(self):
        e = _legacy_latest_entry(AAPL, "EarningsPerShareDiluted")
        self.assertEqual(e["val"], 6.88)
        self.assertGreater(_days(e), 250)          # ~9 months, not a quarter

    def test_legacy_rule_picked_fy2018_revenue(self):
        e = _legacy_latest_entry(AAPL, "Revenues")
        self.assertEqual(e["val"], 265595000000)
        self.assertEqual(e["fy"], 2018)


class TestSelectFactOnRealData(unittest.TestCase):

    def test_eps_is_the_q3_quarter_not_the_ytd(self):
        f = select_fact(AAPL, "EarningsPerShareDiluted")
        self.assertEqual(f.value, 2.02)
        self.assertEqual(f.period_kind, "quarter")
        self.assertEqual((f.fy, f.fp, f.form, f.frame), (2026, "Q3", "10-Q", "CY2026Q2"))
        self.assertEqual(f.unit, "USD/shares")
        self.assertTrue(f.accn)

    def test_revenue_comes_from_the_current_tag_not_the_retired_one(self):
        f = select_fact(AAPL, "Revenues")
        self.assertEqual(f.tag, "RevenueFromContractWithCustomerExcludingAssessedTax")
        self.assertEqual(f.concept, "Revenues")
        self.assertEqual(f.value, 109417000000)
        self.assertEqual(f.end, "2026-06-27")

    def test_assets_is_a_point_in_time_value(self):
        f = select_fact(AAPL, "Assets")
        self.assertEqual(f.value, 383266000000)
        self.assertEqual(f.period_kind, "instant")
        self.assertEqual(f.period_label, "as of 2026-06-27")

    def test_explicit_annual_basis(self):
        f = select_fact(AAPL, "NetIncomeLoss", basis="annual")
        self.assertEqual(f.period_kind, "annual")
        self.assertEqual(f.fp, "FY")
        self.assertIn("12 months ending", f.period_label)

    def test_latest_value_now_agrees_with_select_fact(self):
        self.assertEqual(latest_value(AAPL, "EarningsPerShareDiluted"), 2.02)
        self.assertEqual(latest_value(AAPL, "Revenues"), 109417000000.0)


class TestSelectionRules(unittest.TestCase):

    def _facts(self, concept, *entries, unit="USD"):
        return {"facts": {"us-gaap": {concept: {"units": {unit: list(entries)}}}}}

    def test_restatement_later_filing_wins(self):
        base = {"start": "2026-03-29", "end": "2026-06-27", "fy": 2026, "fp": "Q3", "form": "10-Q"}
        facts = self._facts(
            "NetIncomeLoss",
            {**base, "val": 100, "filed": "2026-07-31", "accn": "orig"},
            {**base, "val": 105, "filed": "2026-11-01", "accn": "restated", "form": "10-Q/A"},
        )
        f = select_fact(facts, "NetIncomeLoss")
        self.assertEqual((f.value, f.accn), (105, "restated"))

    def test_quarter_recognized_by_duration_when_frame_missing(self):
        facts = self._facts(
            "NetIncomeLoss",
            {"start": "2026-03-29", "end": "2026-06-27", "val": 29, "form": "10-Q"},  # no frame
            {"start": "2025-09-28", "end": "2026-06-27", "val": 101, "form": "10-Q"},  # ytd
        )
        f = select_fact(facts, "NetIncomeLoss")
        self.assertEqual((f.value, f.period_kind), (29, "quarter"))

    def test_ytd_is_only_a_last_resort_and_is_labeled(self):
        facts = self._facts(
            "NetIncomeLoss",
            {"start": "2025-09-28", "end": "2026-06-27", "val": 101, "form": "10-Q"},
        )
        f = select_fact(facts, "NetIncomeLoss")
        self.assertEqual(f.period_kind, "ytd")
        self.assertEqual(f.period_label, "9 months ending 2026-06-27 (year-to-date)")

    def test_entries_without_metadata_degrade_to_unknown_period(self):
        facts = self._facts("Assets", {"end": "2025-09-30", "val": 1.0}, {"end": "2026-09-30", "val": 2.0})
        f = select_fact(facts, "Assets")
        self.assertEqual((f.value, f.period_kind, f.period_label), (2.0, "unknown", "period unknown"))

    def test_absent_concept_is_none(self):
        self.assertIsNone(select_fact(AAPL, "GrossProfit"))
        self.assertIsNone(latest_value(AAPL, "GrossProfit"))

    def test_fact_is_frozen_and_serialisable(self):
        f = select_fact(AAPL, "Assets")
        self.assertIsInstance(f, Fact)
        with self.assertRaises(AttributeError):
            f.value = 0  # type: ignore[misc]
        d = f.to_dict()
        self.assertEqual(d["period_label"], "as of 2026-06-27")
        json.dumps(d)


class TestContextLinesCarryPeriodAndUnit(unittest.TestCase):

    def test_financial_context_lines(self):
        lines = summarize_facts("AAPL", AAPL).splitlines()
        self.assertEqual(lines[0], "Ticker: AAPL")
        revenue = next(l for l in lines if l.startswith("Revenues: "))
        self.assertTrue(revenue.startswith("Revenues: 109417000000.0 USD ("))
        self.assertIn("FY2026 Q3", revenue)
        self.assertIn("3 months ending 2026-06-27", revenue)
        self.assertIn("tag RevenueFromContractWithCustomerExcludingAssessedTax", revenue)

    def test_earnings_context_lines_value_stays_first(self):
        # The legacy UI's parseContext/parseNumeric reads the leading number after
        # the first ":" — the value must stay first for input-provenance to work.
        for line in summarize_earnings_facts("AAPL", AAPL).splitlines()[2:]:
            concept, rest = line.split(": ", 1)
            float(rest.split()[0])
        eps = next(l for l in summarize_earnings_facts("AAPL", AAPL).splitlines()
                   if l.startswith("EarningsPerShareDiluted: "))
        self.assertTrue(eps.startswith("EarningsPerShareDiluted: 2.02 USD/shares ("))


if __name__ == "__main__":
    unittest.main()
