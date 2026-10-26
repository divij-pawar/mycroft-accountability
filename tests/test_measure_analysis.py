"""
The measurement harness (scripts/measure_analysis_format.py), driven by scripted models.

It pins what the harness counts and writes, so a measured rate in the log means what it says. The
rates themselves come from live runs and are not tested here.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from financial_agent import prompts
from scripts import measure_analysis_format as M
from tests.test_fa_agent import SIGNAL, Scripted, _bundle, good_run, reply


def right_signal():
    return {**SIGNAL, "cited_figures": [{"name": "rsi_14", "value": 53.78, "unit": "index", "ref": {"call": 2}}]}


def good_right():
    return [*good_run()[:2], reply(right_signal())]


class TestFailureKind(unittest.TestCase):
    def test_a_failure_is_reduced_to_its_class_so_reasons_can_be_counted(self):
        self.assertEqual(M.failure_kind("the reply has no <signal> block directly after <conclusion>"),
                         "the reply has no <signal> block directly after <conclusion>")
        self.assertEqual(M.failure_kind("the <signal> block is not valid JSON (Expecting value)"), "the <signal> block is not valid JSON")
        self.assertEqual(M.failure_kind("the <signal> block is invalid: signal 'Buy' is not one of x"), "the <signal> block is invalid")
        self.assertEqual(M.failure_kind(None), "unknown")


class TestMeasure(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        tmp = Path(self._tmp)
        self._patches = [patch.object(M, "LOGS", tmp), patch.object(M, "OUT_JSON", tmp / "m.json"),
                         patch.object(M, "OUT_MD", tmp / "m.md"), patch.object(M, "OUT_MODELS", tmp / "models.json")]
        for p in self._patches:
            p.start()
        self.bundle = _bundle()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def run_with(self, scripts_by_model, runs):
        queues = {m: list(s) for m, s in scripts_by_model.items()}

        def step_for(model, temperature, seed):
            return Scripted(queues[model].pop(0))

        return M.measure(list(scripts_by_model), ["AAPL"], runs, step_for=step_for,
                         bundle_for=lambda t: self.bundle, progress=lambda s: None)

    def test_each_outcome_is_counted_once(self):
        rows, meta = self.run_with({"m1": [good_right(), ["nope", *good_right()], ["nope", "still nope"]]}, 3)
        self.assertEqual([r["outcome"] for r in rows], ["passed_first", "recovered", "halted"])
        s = M.summarize(rows)["m1"]
        self.assertEqual((s["completed"], s["passed_first_attempt"], s["recovered_on_retry"], s["halted"]), (3, 1, 1, 1))
        self.assertEqual((s["first_attempt_rate"], s["final_rate"]), (0.333, 0.667))
        self.assertEqual(sum(s["failure_reasons"].values()), 3)   # one failed first reply, then two failed replies in the halted run

    def test_figures_are_counted_by_how_they_were_quoted(self):
        rows, _ = self.run_with({"m1": [good_right(), good_run()]}, 2)   # run 2 quotes RSI as 58.2
        s = M.summarize(rows)["m1"]
        self.assertEqual((s["figures_checked"], s["figures_grounded"], s["figures_mismatched"]), (2, 1, 1))
        self.assertEqual((s["runs_with_a_mismatch"], s["runs_with_a_signal_that_cited_figures"]), (1, 2))

    def test_a_model_that_cannot_be_reached_is_an_error_row_not_a_crash(self):
        def down(model, temperature, seed):
            def step(messages):
                raise ConnectionError("model server down")
            return step
        rows, _ = M.measure(["m1"], ["AAPL"], 2, step_for=down, bundle_for=lambda t: self.bundle, progress=lambda s: None)
        self.assertEqual([r["outcome"] for r in rows], ["error", "error"])
        self.assertIn("ConnectionError", rows[0]["error"])
        s = M.summarize(rows)["m1"]
        self.assertEqual((s["runs"], s["completed"], s["errors"], s["first_attempt_rate"]), (2, 0, 2, None))

    def test_questions_and_seeds_vary_and_the_ticker_is_in_the_question(self):
        rows, _ = self.run_with({"m1": [good_right() for _ in range(3)]}, 3)
        self.assertEqual(len({r["question"] for r in rows}), 3)
        self.assertEqual([r["seed"] for r in rows], [1000, 1001, 1002])
        self.assertTrue(all("AAPL" in r["question"] for r in rows))

    def test_the_outputs_are_written_after_every_run_and_agree_with_each_other(self):
        rows, meta = self.run_with({"m1": [good_right(), ["nope", "nope"]]}, 2)
        full = json.loads(M.OUT_JSON.read_text())
        self.assertEqual(len(full["runs"]), 2)
        self.assertEqual(full["meta"]["prompt_version"], prompts.ANALYSIS_PROMPT_VERSION)
        self.assertEqual(full["meta"]["data_as_of"], {"AAPL": "2026-10-05"})
        compact = json.loads(M.OUT_MODELS.read_text())["models"]["m1"]
        self.assertEqual((compact["runs"], compact["first_attempt_passed"], compact["halted"]), (2, 1, 1))
        md = M.OUT_MD.read_text()
        self.assertIn("does not say whether any signal is right", md)
        self.assertIn("| `m1` | 2 | 1 | 0 | 1 | 0 |", md)

    def test_several_models_are_summarised_separately(self):
        rows, _ = self.run_with({"a": [good_right()], "b": [["x", "y"]]}, 1)
        s = M.summarize(rows)
        self.assertEqual((s["a"]["halted"], s["b"]["halted"]), (0, 1))
        self.assertEqual(sorted(s), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
