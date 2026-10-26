"""
The analysis planted-error benchmark (scripts/planted_analysis_errors.py): deterministic, and what it counts.

Network-free and model-free. The counts pinned here are the benchmark's own result on the recorded AAPL
bundle; if a tool's output or the checker changes them, this fails and the audit report is rewritten.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import planted_analysis_errors as P


class TestAlterations(unittest.TestCase):
    def test_digit_swap_keeps_the_places_and_changes_the_number(self):
        self.assertEqual(P.digit_swap(53.7839), 35.7839)
        self.assertEqual(P.digit_swap(34400850), 43400850)
        self.assertEqual(P.digit_swap(2.02), 2.2)
        self.assertIsNone(P.digit_swap(11.11))
        self.assertIsNone(P.digit_swap(0))

    def test_the_precision_of_an_altered_value_is_the_figures_own(self):
        self.assertEqual(P._same_precision(53.7839 * 1.1, 4), round(53.7839 * 1.1, 4))
        self.assertEqual(P._same_precision(34400850 * 1.01, 0), float(round(34400850 * 1.01)))


class TestBenchmark(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = P.run()
        cls.s = cls.result["summary"]

    def test_every_correct_figure_passes_as_written_so_every_trial_starts_from_a_pass(self):
        self.assertEqual(self.result["figures_not_readable"], [])
        self.assertEqual(self.result["figures"], self.result["figures_usable"])
        self.assertGreater(self.result["figures"], 30)

    def test_no_control_is_wrongly_caught(self):
        self.assertEqual(self.s["controls_wrongly_caught"], 0)
        self.assertGreater(self.s["controls"], 60)

    def test_these_errors_are_always_caught(self):
        for kind in ("plus_10pct", "minus_10pct", "plus_1pct", "digit_swap", "unit_slip", "sign_flip", "wrong_call", "missing_call"):
            k = self.s["by_kind"][kind]
            self.assertEqual(k.get("missed", 0), 0, kind)
            self.assertEqual(k["caught"], k["total"], kind)

    def test_the_known_misses_are_exactly_a_figure_quoting_its_siblings_value_under_an_ambiguous_name(self):
        missed = [t for t in self.result["trials"] if t["outcome"] == "missed"]
        self.assertEqual({t["kind"] for t in missed}, {"neighbour_value"})
        self.assertEqual({t["name"] for t in missed}, {"macd_12_26_9_macd", "macd_12_26_9_signal"})

    def test_the_totals_are_what_the_report_says(self):
        self.assertEqual((self.s["errors"], self.s["caught"], self.s["missed"]), (229, 227, 2))
        self.assertEqual(self.s["controls"], 68)

    def test_two_runs_give_identical_results(self):
        self.assertEqual(json.dumps(P.run(), sort_keys=True), json.dumps(self.result, sort_keys=True))

    def test_the_report_states_what_it_does_not_measure(self):
        md = P.to_markdown(self.result)
        self.assertIn("says nothing about how often a real agent makes these", md)
        self.assertIn("227 of 229 caught", md)
        self.assertIn("neighbour_value", md)

    def test_the_outputs_are_written_and_agree(self):
        tmp = tempfile.mkdtemp()
        try:
            with patch.object(P, "LOGS", Path(tmp)), patch.object(P, "OUT_MD", Path(tmp) / "a.md"), patch.object(P, "OUT_JSON", Path(tmp) / "a.json"), \
                    patch("sys.argv", ["planted_analysis_errors.py"]):
                self.assertEqual(P.main(), 0)
            self.assertEqual(json.loads((Path(tmp) / "a.json").read_text())["summary"]["errors"], 229)
            self.assertIn("# Planted-error audit: analyses", (Path(tmp) / "a.md").read_text())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
