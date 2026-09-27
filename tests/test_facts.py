"""
validation/facts.py — canonical, period-aware figure comparison (B1).

Known-answer tests first: every expected tag, period and status below was decided
before the code ran, several from real stored conclusions. The corpus class then
pins the measured behaviour on the labeled real-run corpus, so a change to the
rules shows up as a changed number rather than a silent drift.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from adapters.fixture_adapter import make_fixture_adapter
from core.schemas import AgentID
from validation.concept_linkage import CONCEPT_KEYWORDS, contradiction_flag_concept_aware
from validation.cross_validation import run_cross_agent_validation
from validation.facts import (
    METRICS, Period, compare_facts, context_facts, contradiction_flag_canonical, extract_facts,
)

_CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "cross_agent_real_runs_corpus.json").read_text(encoding="utf-8")
)


def _tags(text, **kw):
    return [(f.metric, f.raw, f.period.label()) for f in extract_facts(text, **kw)]


def _rows(a, b, **kw):
    return {r.label: r for r in contradiction_flag_canonical(a, b, **kw)[2]}


class TestExtraction(unittest.TestCase):

    def test_real_fabrication_conclusion(self):
        # The corpus's one confirmed true positive (f4a4c782), agent A.
        text = next(r for r in _CORPUS["runs"] if r["run_id"].startswith("f4a4c782"))["agent_a_conclusion"]
        self.assertEqual(
            [(m, raw) for m, raw, _ in _tags(text)],
            [("total_assets", "$383.266 billion"), ("revenue", "$265.595 billion"),
             ("net_income", "$101.46 billion"), ("debt_to_equity", "0.34")],
        )

    def test_nearest_alias_in_the_same_clause(self):
        self.assertEqual(
            [m for m, _, _ in _tags("Revenue of $94B and net income of $23B.")],
            ["revenue", "net_income"],
        )
        # Alias after the figure, in its own clause: the preceding clause's alias must not win.
        self.assertEqual(
            [m for m, _, _ in _tags("The company reported $94B in revenue and $23B in net income.")],
            ["revenue", "net_income"],
        )

    def test_longer_alias_wins_a_tie(self):
        self.assertEqual(_tags("Diluted EPS of $2.02.")[0][0], "eps_diluted")

    def test_current_assets_are_not_total_assets(self):
        self.assertEqual(_tags("Current assets of $150B.")[0][0], "current_assets")

    def test_percent_next_to_a_level_metric_is_a_change(self):
        self.assertEqual(_tags("Revenue was up 16% year over year.")[0][0], "revenue_change")

    def test_self_rated_confidence_is_not_a_figure(self):
        # Observed live 2026-09-24: NVDA was flagged as a conflict on this alone.
        self.assertEqual(_tags("Revenue was $96.2 billion. Confidence level: 90%.")[-1][0], "revenue")
        self.assertEqual(len(_tags("Confidence level: 90%.")), 0)

    def test_numbers_inside_citations_and_urls_are_ignored(self):
        self.assertEqual(_tags("[SOURCE: Report v1.5, https://x.example/a/2.4] No figures here."), [])

    def test_unnamed_ratio(self):
        self.assertEqual(_tags("The asset-to-net-income ratio of 12.6% is high.")[0][0], "unnamed_ratio")

    def test_context_lines_parse_as_inputs(self):
        ctx = ("Ticker: AAPL\n"
               "Assets: 383266000000.0 USD (FY2026 Q3, as of 2026-06-27, 10-Q)\n"
               "NetIncomeLoss: 29789000000.0 USD (FY2026 Q3, 3 months ending 2026-06-27, 10-Q)")
        self.assertEqual(
            [(f.metric, f.value) for f in context_facts(ctx)],
            [("total_assets", 383266000000.0), ("net_income", 29789000000.0)],
        )


class TestPeriods(unittest.TestCase):

    def test_period_expressions(self):
        cases = {
            "Q3 FY2026 revenue was $109.4B.": Period("quarter", 2026, 3),
            "FY2026 Q3 revenue was $109.4B.": Period("quarter", 2026, 3),
            "Revenue in the third quarter of 2026 was $109.4B.": Period("quarter", 2026, 3),
            "Fiscal 2025 revenue was $391B.": Period("annual", 2025),
            "TTM revenue was $400B.": Period("ttm"),
            "Revenue for the nine months ended June was $300B.": Period("ytd"),
            "Revenue was $109.4B.": Period(),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_facts(text)[0].period, expected)

    def test_missing_year_is_compatible_not_different(self):
        self.assertTrue(Period("quarter", None, 3).compatible(Period("quarter", 2026, 3)))
        self.assertFalse(Period("quarter", 2026, 2).compatible(Period("quarter", 2026, 3)))


class TestComparison(unittest.TestCase):

    def test_same_figure_different_formatting_matches(self):
        # Real live MSFT run, 2026-09-24: the two agents wrote Q3 revenue differently.
        rows = _rows("Q3 FY2026 revenue was 82,886,000,000.0 USD.", "Q3 FY2026 revenue of $82.9 billion.")
        self.assertEqual(rows["Revenue"].status, "MATCH")

    def test_per_share_tolerance_is_a_cent(self):
        self.assertEqual(_rows("Diluted EPS of $2.02.", "Diluted EPS of $2.03.")["Diluted EPS"].status, "MISMATCH")
        self.assertEqual(_rows("Diluted EPS of $2.02.", "Diluted EPS was 2.02.")["Diluted EPS"].status, "MATCH")

    def test_period_rules(self):
        self.assertEqual(
            _rows("Q3 FY2026 revenue was $109.4B.", "Q2 FY2026 revenue was $94.9B.")["Revenue"].status,
            "DIFFERENT_PERIODS")
        self.assertEqual(
            _rows("Q3 FY2026 revenue was $109.4B.", "Revenue was $94.9B.")["Revenue"].status,
            "UNVERIFIABLE_PERIOD")
        both_unstated = _rows("Revenue was $109.4B.", "Revenue was $94.9B.")["Revenue"]
        self.assertEqual(both_unstated.status, "MISMATCH")
        self.assertIn("Neither agent stated a period", both_unstated.note)

    def test_unspecified_eps_pairs_with_the_specific_one(self):
        rows = _rows("EPS was $2.02.", "Diluted EPS of $2.02 and basic EPS of $2.03.")
        self.assertEqual(rows["Diluted EPS"].status, "MATCH")

    def test_same_side_unspecified_eps_merges_into_the_specific_one(self):
        # Live MSFT run, 2026-09-24: "Diluted EPS of $4.27 … EPS $4.27" was one figure, not two rows.
        _, _, rows = contradiction_flag_canonical("Assets of $758B.", "Diluted EPS of $4.27. EPS was $4.27.")
        self.assertEqual([r.label for r in rows if "EPS" in r.label], ["Diluted EPS"])

    def test_market_data_is_named_and_never_counts_as_an_input(self):
        # Also live MSFT: search-sourced market cap and price read as "Unrecognised figure".
        _, _, rows = contradiction_flag_canonical("Market cap of $3.67 trillion; the stock price of $494.47.", "EPS of $4.27.")
        by = {r.label: r.status for r in rows}
        self.assertEqual(by["Market capitalization"], "UNCORROBORATED")
        self.assertEqual(by["Share price"], "UNCORROBORATED")

    def test_per_share_suffix_makes_it_eps_whatever_precedes_it(self):
        # Live AAPL run: the two agents' sentences for the same $2.02 figure.
        rows = _rows("Revenue of $466.82 billion and net income loss of $2.02 per share.",
                     "Apple's Q3 2026 earnings were $2.02 per share.")
        self.assertEqual(rows["EPS (basic or diluted not stated)"].status, "MATCH")
        self.assertNotIn("Net income", rows)

    def test_stock_moves_are_share_price_changes(self):
        self.assertEqual(_tags("The stock slid more than 6% in extended trading.")[0][0], "share_price_change")

    def test_lens_metric_on_one_side_is_expected(self):
        self.assertEqual(_rows("Assets of $383B.", "Operating income of $35.7B.")["Total assets"].status, "ONE_SIDED")
        flag, _, _ = contradiction_flag_canonical("Assets of $383B.", "Operating income of $35.7B.")
        self.assertFalse(flag)


class TestDerivations(unittest.TestCase):

    def test_correct_ratio_from_own_figures(self):
        # Real GOOGL corpus conclusion (2c3c4f23): 174.771 / 921.983 = 18.96%.
        rows = _rows("Assets of $921,983,000,000.00 and net income of $174,771,000,000.00, indicating a "
                     "Return on Assets (ROA) of 18.85%.", "EPS of $5.80.")
        self.assertEqual(rows["Return on assets"].status, "DERIVED_OK")

    def test_wrong_ratio_is_an_internal_error_not_a_contradiction(self):
        # Real AAPL corpus conclusion (56965308): 265.595 / 383.266 = 0.693, not 0.13.
        a = ("Assets of $383,266,000,000, revenues of $265,595,000,000. "
             "The asset turnover ratio of 0.13 indicates efficient use of assets.")
        flag, _, rows = contradiction_flag_canonical(a, "EPS of $6.08.")
        row = next(r for r in rows if r.label == "Asset turnover")
        self.assertEqual(row.status, "DERIVED_WRONG")
        self.assertIn("0.693", row.note)
        self.assertFalse(flag)

    def test_ratio_without_any_components_stays_uncorroborated(self):
        # The fabrication shape: nothing the agent was given can produce a D/E ratio.
        flag, divergent, _ = contradiction_flag_canonical("The debt-to-equity ratio of 0.34.", "EPS of $6.08.")
        self.assertTrue(flag)
        self.assertEqual(divergent, ["0.34"])

    def test_context_inputs_make_a_ratio_checkable(self):
        ctx = "Assets: 400000000000.0 USD (FY2026 Q3)\nNetIncomeLoss: 30000000000.0 USD (FY2026 Q3)"
        flag, _, rows = contradiction_flag_canonical("Return on assets (ROA) of 7.5%.", "EPS of $2.", context_a=ctx)
        self.assertEqual(next(r for r in rows if r.label == "Return on assets").status, "DERIVED_OK")
        self.assertFalse(flag)


class TestYears(unittest.TestCase):

    def test_release_years_compared_when_enabled(self):
        rows = _rows("Inception was released in 2010.", "Inception was released in 2015.", include_years=True)
        self.assertEqual(rows["Release year"].status, "MISMATCH")
        rows = _rows("Inception was released in 2010.", "The film came out in 2010.", include_years=True)
        self.assertEqual(rows["Release year"].status, "MATCH")

    def test_years_ignored_by_default_and_period_years_never_become_values(self):
        self.assertEqual(_tags("Inception was released in 2010."), [])
        self.assertEqual([m for m, _, _ in _tags("FY2026 revenue was $400B.", include_years=True)], ["revenue"])


class TestWiring(unittest.TestCase):

    def _run(self, a, b, **kw):
        return run_cross_agent_validation(
            "s", "ctx", "ctx", AgentID.GENERIC_A, AgentID.GENERIC_B,
            make_fixture_adapter(a), make_fixture_adapter(b), **kw,
        )[0]

    def test_rows_are_computed_whatever_rule_decides_the_flag(self):
        result = self._run("Revenue was $4.20 million.", "Revenue was $5.10 million.")
        self.assertEqual(result.contradiction_rule, "symmetric_difference")
        self.assertEqual(result.metric_comparisons[0]["status"], "MISMATCH")
        self.assertTrue(result.metric_comparisons[0]["flags"])

    def test_canonical_rule_catches_the_bare_year(self):
        result = self._run("The film was released in 2014.", "The film was released in 2015.",
                           contradiction_rule="canonical_facts", include_years=True)
        self.assertTrue(result.contradiction_flag)
        self.assertEqual(result.divergent_numbers, ["2014", "2015"])


class TestSingleSourceOfAliases(unittest.TestCase):

    def test_concept_linkage_keywords_are_all_known_aliases(self):
        """concept_linkage.py keeps its measured keyword table; this pins it to METRICS."""
        by_tag = {tag: m for m in METRICS for tag in m.xbrl_tags}
        for concept, keywords in CONCEPT_KEYWORDS.items():
            metric = by_tag[concept]
            for kw in keywords:
                with self.subTest(concept=concept, keyword=kw):
                    self.assertTrue(any(kw in alias or alias in kw for alias in metric.aliases),
                                    f"{kw!r} ({concept}) has no counterpart in {metric.name}'s aliases")


class TestAgainstLabeledCorpus(unittest.TestCase):
    """Measured 2026-09-24; see logs/RUN_LOG.md's B1 entry for the reading of these numbers."""

    @classmethod
    def setUpClass(cls):
        cls.results = []
        for run in _CORPUS["runs"]:
            a, b = run.get("agent_a_conclusion"), run.get("agent_b_conclusion")
            if a and b:
                cls.results.append((run, contradiction_flag_canonical(a, b), contradiction_flag_concept_aware(a, b)[0]))

    def test_preserves_the_confirmed_true_positive(self):
        run, (flag, divergent, _), _ = next(x for x in self.results if x[0]["run_id"].startswith("f4a4c782"))
        self.assertTrue(flag)
        self.assertIn("0.34", divergent)

    def test_flags_exactly_these_disjoint_concept_runs(self):
        flagged = sorted(r["run_id"][:8] for r, (flag, _, _), _ in self.results
                         if r["label"] == "disjoint_concepts" and flag)
        # f4a4c782 is the true positive; the other five state a ratio (ROA, "asset-to-
        # net-income") with no components in the conclusion, and the corpus never
        # recorded the agents' contexts — so they can be neither confirmed nor cleared.
        self.assertEqual(flagged, ["2db3762c", "515f263a", "a1cbd371", "ba546346", "f4a4c782", "ff344935"])

    def test_no_flags_where_there_is_nothing_to_compare(self):
        for r, (flag, _, _), _ in self.results:
            if r["label"] in ("mock_smoke_test", "no_numbers_either_side"):
                self.assertFalse(flag, r["run_id"])

    def test_finds_the_wrong_asset_turnover_in_both_aapl_runs(self):
        wrong = sorted(r["run_id"][:8] for r, (_, _, rows), _ in self.results
                       if any(row.status == "DERIVED_WRONG" for row in rows))
        self.assertEqual(wrong, ["56965308", "8c67de62"])

    def test_concept_aware_remains_better_on_this_corpus_hence_opt_in(self):
        """The rule the roadmap set: worse on the corpus → opt-in for the financial pairing."""
        ca = sum(1 for r, _, ca_flag in self.results if r["label"] == "disjoint_concepts" and ca_flag)
        cf = sum(1 for r, (flag, _, _), _ in self.results if r["label"] == "disjoint_concepts" and flag)
        self.assertEqual((ca, cf), (1, 6))


if __name__ == "__main__":
    unittest.main()
