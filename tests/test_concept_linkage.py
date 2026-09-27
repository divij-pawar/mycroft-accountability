"""
Tests for validation/concept_linkage.py — a PROTOTYPE, not a replacement for
validation/cross_validation.py's contradiction_flag. See that module's docstring
and divij/cross-agent-validation-disjoint-concepts-diagnosis.md for why this
exists: the diagnosis's own conclusion was that fixing the disjoint-concepts
over-flag needs "either tag extracted numbers with the concept they came from,
or ... give the two producers some genuinely overlapping concepts." This file
measures the first option against the real-run corpus, rather than assuming it
works.

Corpus caveat carried forward
    tests/fixtures/cross_agent_real_runs_corpus.json's per-run "label" is an
    AI-assigned judgment pending human review (see its own _meta.labeling_caveat
    and tests/test_real_run_corpus.py). The counts asserted below are measured
    against those labels, so they inherit the same caveat.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from validation.concept_linkage import contradiction_flag_concept_aware, tag_numbers

_CORPUS_PATH = Path(__file__).resolve().parent / "fixtures" / "cross_agent_real_runs_corpus.json"


def _load_corpus() -> dict:
    with open(_CORPUS_PATH, encoding="utf-8") as f:
        return json.load(f)


class TestTagNumbers(unittest.TestCase):
    """Unit-level checks on the tagging heuristic, isolated from the corpus."""

    def test_natural_language_phrase_is_tagged(self):
        tagged = tag_numbers("Assets of $383.266 billion were reported.")
        self.assertIn(("$383.266 billion", "Assets"), tagged)

    def test_untagged_number_has_no_known_concept_nearby(self):
        tagged = tag_numbers("The debt-to-equity ratio of 0.34 indicates leverage.")
        self.assertEqual(tagged, [("0.34", None)])

    def test_raw_xbrl_tag_name_is_tagged_even_though_it_has_no_spaces(self):
        # Regression for a real corpus case (AAPL run 8c67de62): the model echoed
        # the literal prompt-context tag name rather than a natural-language
        # phrase, because producers/lens.py's context format is "ConceptName: value".
        tagged = tag_numbers(
            "The difference between EarningsPerShareDiluted (6.88) and "
            "EarningsPerShareBasic (6.91) is minor."
        )
        self.assertIn(("6.88", "EarningsPerShareDiluted"), tagged)
        self.assertIn(("6.91", "EarningsPerShareBasic"), tagged)

    def test_decimal_point_in_a_number_does_not_end_the_sentence(self):
        # Regression: an earlier version split "sentences" on any '.', so the
        # decimal point inside "14.41" was mistaken for a sentence boundary and
        # truncated the sentence right before "14.24" — hiding the "diluted eps"
        # keyword that appeared earlier in the same sentence. Real corpus case:
        # GOOGL run 2c3c4f23.
        tagged = tag_numbers(
            "The slight difference between Basic and Diluted EPS (14.41 vs 14.24) "
            "is within typical ranges."
        )
        concepts = dict(tagged)
        self.assertEqual(concepts["14.41"], "EarningsPerShareDiluted")
        self.assertEqual(concepts["14.24"], "EarningsPerShareDiluted")


class TestConceptAwareFlagAgainstCorpus(unittest.TestCase):
    """
    The actual question this prototype exists to answer: does excluding
    known-concept-tagged numbers from comparison kill the disjoint-concepts
    false positives without losing the one confirmed true positive?

    Every count below was measured by running this module against the corpus,
    not decided in advance — if a future edit to concept_linkage.py or the
    corpus changes these numbers, that is real news about the prototype's
    behavior. Update the assertion deliberately (with an explanation of what
    changed and why), don't silence it.
    """

    @classmethod
    def setUpClass(cls):
        cls.corpus = _load_corpus()

    def _replay_all(self):
        results = []
        for run in self.corpus["runs"]:
            a, b = run.get("agent_a_conclusion"), run.get("agent_b_conclusion")
            if not a or not b:
                continue
            flag, divergent, _tagged = contradiction_flag_concept_aware(a, b)
            results.append((run, flag, divergent))
        return results

    def test_kills_fifteen_of_sixteen_disjoint_concept_false_positives(self):
        still_flagged = [
            run["run_id"] for run, flag, _div in self._replay_all()
            if run["label"] == "disjoint_concepts" and flag
        ]
        # The one survivor is the historic true positive (see the next test) —
        # it is labeled disjoint_concepts in the corpus because Producer B cited
        # no numbers at all in that run, not because the finding is spurious.
        self.assertEqual(
            still_flagged, ["f4a4c782-65ae-4c11-8ccb-75b0b7305e94"],
            "The set of disjoint_concepts runs still flagging under concept-linkage "
            "changed — re-examine before updating this assertion.",
        )

    def test_preserves_the_one_confirmed_true_positive(self):
        fabrication_run = next(
            run for run in self.corpus["runs"]
            if run["notes"].get("historic_fabrication_case")
        )
        flag, divergent, tagged = contradiction_flag_concept_aware(
            fabrication_run["agent_a_conclusion"], fabrication_run["agent_b_conclusion"]
        )
        self.assertTrue(
            flag,
            "Concept-linkage suppressed the one real fabrication this subsystem has "
            "ever caught (AAPL's 0.34 debt-to-equity ratio) — that would make this "
            "prototype strictly worse than concepts_expected_to_overlap=False, which "
            "already has this exact failure mode (see the diagnosis doc's finding 3a).",
        )
        self.assertIn("0.34", divergent)

    def test_introduces_no_new_false_positives_on_non_disjoint_labels(self):
        newly_flagged = [
            (run["run_id"], run["label"]) for run, flag, _div in self._replay_all()
            if flag and run["label"] not in ("disjoint_concepts", "genuine_conflict")
        ]
        self.assertEqual(
            newly_flagged, [],
            f"Concept-linkage flagged {len(newly_flagged)} run(s) that were not "
            "previously flagged for a numeric-disjoint reason — inspect before "
            "assuming this prototype is a strict improvement.",
        )

    def test_zero_genuine_conflicts_to_measure_against(self):
        # Sanity check on the premise, not the prototype: as of this corpus,
        # there is no genuine_conflict-labeled run to confirm this design would
        # actually flag correctly if the underlying vocabularies ever overlapped.
        # See tests/test_real_run_corpus.py's identical assertion and item 5 of
        # this period's requested work (scripts/run_overlap_concept_live.py) for
        # the live test that exists specifically to produce one.
        genuine = [r for r in self.corpus["runs"] if r["label"] == "genuine_conflict"]
        self.assertEqual(genuine, [])


if __name__ == "__main__":
    unittest.main()
