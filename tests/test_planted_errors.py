"""
The planted-error benchmark (scripts/planted_errors.py) plants what it says and counts what it sees.

What this locks in
    - Each alteration changes the figure the way its name says, and the controls keep (or only
      round) the value.
    - On a real recorded run (AAPL 20c538e4): a 10% error planted in a figure the agent was given
      from the filing stops the run, by the source check; the same value rewritten does not.
    - A figure in a "…, respectively" list is tagged to the metric named in its position, so an
      error planted in it is caught (it used to be missed).
    - The benchmark is deterministic, and its summary adds up.

No model and no network: it replays the deterministic comparison, checks and gate over the
recorded runs in web/frontend/tests/fixtures/.
"""

from __future__ import annotations

import unittest

from scripts import planted_errors as pe


class AlterationTest(unittest.TestCase):
    def test_errors_change_the_value(self):
        self.assertEqual(pe.ALTERATIONS["plus_10pct"][0]("$29.79 billion"), "$32.77 billion")
        self.assertEqual(pe.ALTERATIONS["plus_1pct"][0]("$2.03"), "$2.05")
        self.assertEqual(pe.unit_slip("$29.79 billion"), "$29.79 million")
        self.assertEqual(pe.digit_swap("$4.27"), "$4.72")

    def test_a_change_too_small_to_show_gains_a_digit(self):
        # 1% of $2.0 rounds back to $2.0 at one decimal, so the alteration adds precision.
        self.assertEqual(pe.ALTERATIONS["plus_1pct"][0]("$2.0"), "$2.02")

    def test_controls_keep_or_round_the_value(self):
        self.assertEqual(pe.same_value("$29.79 billion"), "$29.790 billion")
        self.assertEqual(pe.rounded("$29.79 billion"), "$29.8 billion")
        self.assertIsNone(pe.rounded("$30 billion"))  # nothing to round away

    def test_no_number_means_no_alteration(self):
        for name, (alter, _) in pe.ALTERATIONS.items():
            self.assertIsNone(alter("revenue grew"), name)


class BenchmarkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bases = pe.load_bases()
        cls.aapl = next(b for b in cls.bases if b.run_id.startswith("20c538e4"))
        cls.trials = pe.run_base(cls.aapl)

    def _trial(self, slot, raw, alteration):
        return next(t for t in self.trials if t.slot == slot and t.original == raw and t.alteration == alteration)

    def test_a_planted_error_in_a_given_figure_stops_the_run(self):
        t = self._trial("a", "$109.4 billion", "plus_10pct")  # revenue, given to agent A from the 10-Q
        self.assertEqual(t.outcome, "stopped")
        self.assertIn("source check", t.by)

    def test_the_same_value_rewritten_is_not_caught(self):
        t = self._trial("a", "$109.4 billion", "same_value")
        self.assertFalse(t.is_error)
        self.assertNotEqual(t.outcome, "stopped")

    def test_a_respectively_list_error_is_caught(self):
        # Until the 2026-10-05 coverage fix this figure was tagged as net income and the error was
        # missed. Paired in order, it is operating income, checked against the filing.
        t = self._trial("b", "$35.695 billion", "plus_10pct")
        self.assertEqual(t.metric, "operating_income")
        self.assertEqual(t.outcome, "stopped")
        self.assertIn("source check", t.by)

    def test_it_is_deterministic(self):
        again = pe.run_base(self.aapl)
        self.assertEqual([(t.alteration, t.original, t.outcome) for t in again],
                         [(t.alteration, t.original, t.outcome) for t in self.trials])

    def test_the_summary_adds_up(self):
        trials = [t for b in self.bases for t in pe.run_base(b)]
        s = pe.summarize(trials)
        e = s["errors"]
        self.assertEqual(e["planted"], e["stopped"] + e["flagged"] + e["missed"])
        self.assertEqual(e["planted"] + s["controls"]["planted"], len(trials))
        md = pe.to_markdown(self.bases, trials, s)
        self.assertIn(f"**Planted errors:** {e['planted']}.", md)

    def test_each_recorded_run_is_used_once(self):
        ids = [b.run_id for b in self.bases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(b.ticker for b in self.bases))


if __name__ == "__main__":
    unittest.main()
