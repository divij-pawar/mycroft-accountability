"""
Regression tests against tests/fixtures/cross_agent_real_runs_corpus.json --
every real (non-mock) cross-agent comparison run persisted to the store as of
2026-09-07, replayed through today's run_cross_agent_validation().

Why this file exists
    "7 of 12 tickers still over-flag" (web/self_report.py's disjoint-concepts
    entry) used to be a claim someone had to re-derive by querying sqlite by
    hand. This corpus and these tests turn it into a versioned artifact: the
    headline finding (zero genuine same-concept conflicts among 31 stored
    real runs) is asserted here, not just written in prose, so it cannot
    silently stop being true. See divij/cross-agent-validation-disjoint-concepts-diagnosis.md
    for the write-up this corpus supports.

What "replay" means here
    Each stored run's agent_a_conclusion / agent_b_conclusion strings are fed
    back through the real comparator via adapters.fixture_adapter -- no live
    model call -- with concepts_expected_to_overlap=False, the same default
    web/server.py's /api/compare route uses. This measures what today's code
    actually does with historical conclusions; it is not a hand computation.

Labeling caveat (see the fixture's _meta.labeling_caveat)
    The "label" field on each corpus entry is an AI-assigned categorical
    judgment, not verified ground truth -- flagged here again per SNICKERDOODLE
    P8 so a reader of just this test file still sees the caveat.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from adapters.fixture_adapter import make_fixture_adapter
from core.schemas import AgentID
from validation.cross_validation import run_cross_agent_validation

_CORPUS_PATH = Path(__file__).resolve().parent / "fixtures" / "cross_agent_real_runs_corpus.json"

_VALID_LABELS = {
    "genuine_conflict",
    "disjoint_concepts",
    "no_numbers_either_side",
    "halted",
    "mock_smoke_test",
}


def _load_corpus() -> dict:
    with open(_CORPUS_PATH, encoding="utf-8") as f:
        return json.load(f)


class TestCorpusStructure(unittest.TestCase):
    """Sanity checks on the fixture itself -- catches a hand-edit that breaks its own contract."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = _load_corpus()

    def test_corpus_loads_and_is_non_empty(self):
        self.assertIn("runs", self.corpus)
        self.assertGreater(len(self.corpus["runs"]), 0)

    def test_every_run_has_a_valid_label(self):
        for run in self.corpus["runs"]:
            self.assertIn(
                run["label"], _VALID_LABELS,
                f"run {run['run_id']} has unrecognised label {run['label']!r}",
            )

    def test_meta_label_counts_match_actual_counts(self):
        actual: dict[str, int] = {}
        for run in self.corpus["runs"]:
            actual[run["label"]] = actual.get(run["label"], 0) + 1
        self.assertEqual(actual, self.corpus["_meta"]["label_counts"])

    def test_carries_the_labeling_caveat(self):
        # The corpus must keep stating its labels are AI judgments pending human
        # review -- this is not decoration, it is the thing that keeps this
        # fixture from being mistaken for attested ground truth (P8).
        self.assertIn("labeling_caveat", self.corpus["_meta"])
        self.assertIn("not reviewed", self.corpus["_meta"]["extracted_by"].lower())


class TestHeadlineFinding(unittest.TestCase):
    """
    The canary for the structural finding: as of this corpus's extraction date,
    zero stored real runs show two agents citing different values for the same
    concept. If this ever becomes non-zero, that is real news -- update the
    corpus deliberately (with the new run's evidence) rather than editing this
    assertion to make it pass.
    """

    @classmethod
    def setUpClass(cls):
        cls.corpus = _load_corpus()

    def test_zero_genuine_conflicts_in_corpus(self):
        genuine = [r for r in self.corpus["runs"] if r["label"] == "genuine_conflict"]
        self.assertEqual(
            genuine, [],
            "A genuine same-concept conflict appeared in the real-run corpus -- "
            "this is the first observed instance of Cross-Agent Validation's stated "
            "purpose actually firing correctly. Update "
            "divij/cross-agent-validation-disjoint-concepts-diagnosis.md, do not just "
            "silence this test.",
        )

    def test_disjoint_concepts_is_the_dominant_flagged_shape(self):
        flagged_today = [
            r for r in self.corpus["runs"]
            if r["replay_today"]["contradiction_flag"] is True
        ]
        non_disjoint = [r for r in flagged_today if r["label"] != "disjoint_concepts"]
        self.assertEqual(
            non_disjoint, [],
            f"{len(non_disjoint)} run(s) flag today for a reason other than "
            "disjoint concepts -- inspect before assuming the diagnosis still holds.",
        )


class TestReplayReproducesRecordedBehaviour(unittest.TestCase):
    """
    Feeds each corpus entry's real stored conclusions back through today's
    run_cross_agent_validation() and checks the result matches what was
    recorded in replay_today at extraction time. A failure here means
    validation/cross_validation.py's behaviour has changed since 2026-09-07 --
    which may be an intended fix (rerun build_corpus.py and update the
    fixture) or a regression (investigate before touching the fixture).
    """

    @classmethod
    def setUpClass(cls):
        cls.corpus = _load_corpus()

    def test_replay_matches_recorded_snapshot(self):
        for run in self.corpus["runs"]:
            a_conclusion = run["agent_a_conclusion"]
            b_conclusion = run["agent_b_conclusion"]
            expected = run["replay_today"]

            with self.subTest(run_id=run["run_id"], subject=run["subject"]):
                if not a_conclusion or not b_conclusion:
                    self.assertEqual(expected["status"], "SKIPPED_NO_CONCLUSION")
                    continue

                result, _ = run_cross_agent_validation(
                    run["subject"], "ctx-a", "ctx-b",
                    AgentID.FINANCIAL, AgentID.EARNINGS,
                    make_fixture_adapter(a_conclusion),
                    make_fixture_adapter(b_conclusion),
                    concepts_expected_to_overlap=False,
                )

                self.assertEqual(result.status.value, expected["status"])
                self.assertEqual(result.contradiction_flag, expected["contradiction_flag"])
                self.assertEqual(result.agent_a_numbers, expected["agent_a_numbers"])
                self.assertEqual(result.agent_b_numbers, expected["agent_b_numbers"])
                self.assertEqual(result.divergent_numbers, expected["divergent_numbers"])

    def test_six_runs_flip_from_stored_true_to_replay_false(self):
        """
        Documents, as an executable check, that the concepts_expected_to_overlap=False
        fix changed 6 historically-flagged runs to unflagged on replay -- and that
        none flip the other direction (false -> true). See the diagnosis doc's
        section on the historic fabrication case (f4a4c782), which is one of the six.
        """
        flips_true_to_false = 0
        flips_false_to_true = 0
        for run in self.corpus["runs"]:
            stored = run["stored_contradiction_flag"]
            today = run["replay_today"]["contradiction_flag"]
            if stored is None or today is None:
                continue
            if stored is True and today is False:
                flips_true_to_false += 1
            elif stored is False and today is True:
                flips_false_to_true += 1

        self.assertEqual(flips_false_to_true, 0)
        self.assertEqual(flips_true_to_false, 6)


class TestConceptAwareRuleAgainstCorpus(unittest.TestCase):
    """
    2026-09-11: contradiction_rule="concept_aware" is wired into
    run_cross_agent_validation() (see its docstring) and is what /api/compare
    now uses for the real Producer A/B pairing. This class proves the wiring
    reproduces exactly what tests/test_concept_linkage.py measured by calling
    validation/concept_linkage.py directly — going through the public
    run_cross_agent_validation() API this time, not the module underneath it.
    """

    @classmethod
    def setUpClass(cls):
        cls.corpus = _load_corpus()

    def _replay_concept_aware(self):
        results = []
        for run in self.corpus["runs"]:
            a, b = run.get("agent_a_conclusion"), run.get("agent_b_conclusion")
            if not a or not b:
                continue
            result, _ = run_cross_agent_validation(
                run["subject"], "ctx-a", "ctx-b",
                AgentID.FINANCIAL, AgentID.EARNINGS,
                make_fixture_adapter(a), make_fixture_adapter(b),
                contradiction_rule="concept_aware",
            )
            results.append((run, result))
        return results

    def test_kills_fifteen_of_sixteen_disjoint_concept_false_positives(self):
        still_flagged = [
            run["run_id"] for run, result in self._replay_concept_aware()
            if run["label"] == "disjoint_concepts" and result.contradiction_flag
        ]
        self.assertEqual(still_flagged, ["f4a4c782-65ae-4c11-8ccb-75b0b7305e94"])

    def test_preserves_the_one_confirmed_true_positive(self):
        fabrication_run = next(
            run for run in self.corpus["runs"]
            if run["notes"].get("historic_fabrication_case")
        )
        result, _ = run_cross_agent_validation(
            fabrication_run["subject"], "ctx-a", "ctx-b",
            AgentID.FINANCIAL, AgentID.EARNINGS,
            make_fixture_adapter(fabrication_run["agent_a_conclusion"]),
            make_fixture_adapter(fabrication_run["agent_b_conclusion"]),
            contradiction_rule="concept_aware",
        )
        self.assertTrue(result.contradiction_flag)
        self.assertIn("0.34", result.divergent_numbers)

    def test_introduces_no_new_false_positives(self):
        newly_flagged = [
            (run["run_id"], run["label"]) for run, result in self._replay_concept_aware()
            if result.contradiction_flag and run["label"] not in ("disjoint_concepts", "genuine_conflict")
        ]
        self.assertEqual(newly_flagged, [])


if __name__ == "__main__":
    unittest.main()
