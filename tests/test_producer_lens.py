"""
Tests for producers/lens.py and the two lens declarations.

Producer A and Producer B used to be two near-identical modules; they are now one
runner plus two `ConceptLens` values. These tests pin the properties that
consolidation could plausibly have broken, and the ones the rest of the system
silently depends on:

  * the exact context format, because the UI's input-provenance check parses it
    back out to decide whether a cited number traces to the agent's own input;
  * the disjointness of the two concept sets, because that information asymmetry
    is the entire premise of Cross-Agent Validation — if the lenses ever overlap,
    a "disagreement" stops meaning what the comparator reports it as meaning;
  * one payload, two lenses, so the shared-fetch story the UI renders stays true.

Network-free: every fetch is injected (`core.contracts.JsonFetcher`).
"""

import unittest
import uuid

from tests.support import make_scripted_adapter
from core.schemas import AgentID
from producers import BY_NAME, LENSES
from producers.earnings import EARNINGS_LENS, summarize_earnings_facts
from producers.financial import FINANCIAL_LENS, summarize_facts
from producers.lens import ConceptLens, run_lens, shared_concepts

_FACTS = {
    "facts": {
        "us-gaap": {
            "Assets": {"units": {"USD": [
                {"end": "2022-09-30", "val": 352755000000.0},
                {"end": "2023-09-30", "val": 383266000000.0},
            ]}},
            "Revenues": {"units": {"USD": [{"end": "2023-09-30", "val": 383285000000.0}]}},
            "EarningsPerShareDiluted": {
                "units": {"USD/shares": [{"end": "2023-09-30", "val": 6.08}]}
            },
        }
    }
}


def _fetch(url: str) -> dict:
    return _FACTS


class TestContextFormat(unittest.TestCase):
    def test_first_line_is_the_ticker(self):
        self.assertTrue(summarize_facts("AAPL", _FACTS).startswith("Ticker: AAPL\n"))

    def test_every_concept_gets_a_line_in_declaration_order(self):
        lines = summarize_facts("AAPL", _FACTS).splitlines()[1:]
        self.assertEqual(
            [line.split(":")[0] for line in lines], list(FINANCIAL_LENS.concepts)
        )

    def test_absent_concept_renders_as_not_reported_rather_than_disappearing(self):
        """
        The context must show what the agent was asked to consider, not only what
        happened to exist — otherwise a missing figure is indistinguishable from a
        concept the lens never requested.
        """
        self.assertIn("NetIncomeLoss: not reported", summarize_facts("AAPL", _FACTS))

    def test_latest_value_wins(self):
        self.assertIn("Assets: 383266000000.0", summarize_facts("AAPL", _FACTS))

    def test_header_appears_only_for_a_lens_that_declares_one(self):
        self.assertEqual(
            summarize_earnings_facts("AAPL", _FACTS).splitlines()[1],
            "Earnings-quality snapshot:",
        )
        self.assertNotIn("snapshot", summarize_facts("AAPL", _FACTS))

    def test_format_is_parseable_back_into_concept_value_pairs(self):
        """
        This is the contract the UI's provenance check relies on: the value is the
        leading token after the first ": ", with unit and period metadata after it
        (B0, 2026-09-24). These fixtures carry only end/val, so the period is
        honestly unknown — tests/test_edgar_facts.py covers real metadata.
        """
        rows = dict(
            line.split(": ", 1)
            for line in summarize_facts("AAPL", _FACTS).splitlines()[1:]
        )
        self.assertEqual(rows["Assets"].split()[0], "383266000000.0")
        self.assertEqual(rows["Assets"], "383266000000.0 USD (period unknown)")
        self.assertEqual(rows["NetIncomeLoss"], "not reported")


class TestInformationAsymmetry(unittest.TestCase):
    # Lens v1 (to 2026-09-25) pinned these two sets as fully disjoint. B2 (lens v2,
    # the human's "overlapping metrics now" decision, logs/RUN_LOG.md 2026-09-24)
    # shares exactly two headline figures and keeps the rest of the asymmetry.
    SHARED = {"NetIncomeLoss", "EarningsPerShareDiluted"}

    def test_the_two_lenses_share_exactly_the_two_headline_figures(self):
        self.assertEqual(set(FINANCIAL_LENS.concepts) & set(EARNINGS_LENS.concepts), self.SHARED)
        self.assertEqual(shared_concepts(FINANCIAL_LENS, EARNINGS_LENS), frozenset(self.SHARED))
        self.assertEqual((FINANCIAL_LENS.version, EARNINGS_LENS.version), ("v2", "v2"))

    def test_each_lens_still_sees_only_its_own_other_concepts(self):
        context_a = summarize_facts("AAPL", _FACTS)
        context_b = summarize_earnings_facts("AAPL", _FACTS)
        for concept in set(EARNINGS_LENS.concepts) - self.SHARED:
            self.assertNotIn(concept, context_a)
        for concept in set(FINANCIAL_LENS.concepts) - self.SHARED:
            self.assertNotIn(concept + ":", context_b)
        for concept in self.SHARED:
            self.assertIn(concept + ":", context_a)
            self.assertIn(concept + ":", context_b)

    def test_v1_context_lines_keep_their_position(self):
        # New concepts were appended, so a v1 reader of line N still reads the same concept.
        self.assertEqual(FINANCIAL_LENS.concepts[:3], ("Assets", "Revenues", "NetIncomeLoss"))
        self.assertEqual(EARNINGS_LENS.concepts[:3],
                         ("EarningsPerShareDiluted", "EarningsPerShareBasic", "OperatingIncomeLoss"))

    def test_one_payload_two_lenses(self):
        """
        Both contexts are derived from the same dict object. The UI renders a single
        shared EDGAR fetch on that basis, so it has to stay true.
        """
        facts = _FACTS
        self.assertIn("Assets", summarize_facts("AAPL", facts))
        self.assertIn("EarningsPerShareDiluted", summarize_earnings_facts("AAPL", facts))

    def test_lenses_have_distinct_identities(self):
        self.assertNotEqual(FINANCIAL_LENS.agent_id, EARNINGS_LENS.agent_id)
        self.assertNotEqual(FINANCIAL_LENS.trace_prefix, EARNINGS_LENS.trace_prefix)
        self.assertEqual(len({lens.name for lens in LENSES}), len(LENSES))


class TestLensIsImmutable(unittest.TestCase):
    def test_a_lens_cannot_be_mutated_mid_run(self):
        """
        A run's audit trail reports the concepts that run read. If a lens were
        mutable, that report would stop being evidence of what actually happened.
        """
        with self.assertRaises(Exception):
            FINANCIAL_LENS.concepts = ("Assets",)


class TestRunLens(unittest.TestCase):
    def test_run_attributed_to_the_lens_agent_id(self):
        result = run_lens(
            EARNINGS_LENS,
            "AAPL",
            "0000320193",
            make_scripted_adapter("none"),
            run_id=uuid.uuid4(),
            fetch_fn=_fetch,
        )
        self.assertEqual(result.reasoning_objects[0].agent_id, AgentID.EARNINGS)

    def test_the_agent_receives_its_own_lens_context(self):
        seen = {}

        def spy(subject, context, directive):
            seen["context"] = context
            return make_scripted_adapter("none")(subject, context, directive)

        run_lens(EARNINGS_LENS, "AAPL", "0000320193", spy, fetch_fn=_fetch)
        self.assertIn("EarningsPerShareDiluted: 6.08", seen["context"])
        self.assertNotIn("Assets", seen["context"])

    def test_a_new_producer_needs_no_new_runner(self):
        """
        The OCP claim, exercised rather than asserted: a third producer is a value.
        """
        cash_lens = ConceptLens(
            name="cashflow",
            agent_id=AgentID.EXTERNAL,
            concepts=("Revenues",),
            header="Cash snapshot:",
        )
        result = run_lens(
            cash_lens, "AAPL", "0000320193", make_scripted_adapter("none"), fetch_fn=_fetch
        )
        self.assertEqual(result.reasoning_objects[0].agent_id, AgentID.EXTERNAL)
        self.assertEqual(
            cash_lens.summarize("AAPL", _FACTS),
            "Ticker: AAPL\nCash snapshot:\nRevenues: 383285000000.0 USD (period unknown)",
        )

    def test_adr07_retry_behaviour_is_identical_across_producers(self):
        """
        There is one call site for the validation loop now, so no producer can drift
        from ADR-07's retry-then-halt path. Assert it for both.
        """
        for lens in LENSES:
            with self.subTest(lens=lens.name):
                result = run_lens(
                    lens, "AAPL", "0000320193",
                    make_scripted_adapter("retry_success"), fetch_fn=_fetch,
                )
                self.assertEqual(len(result.reasoning_objects), 2)


class TestRegistry(unittest.TestCase):
    def test_lenses_are_reachable_by_name(self):
        self.assertIs(BY_NAME["financial"], FINANCIAL_LENS)
        self.assertIs(BY_NAME["earnings"], EARNINGS_LENS)

    def test_producer_order_is_a_then_b(self):
        # B4 added the bull/bear pairing after the original A/B pair.
        self.assertEqual([lens.name for lens in LENSES], ["financial", "earnings", "bull", "bear"])

    def test_bull_and_bear_see_the_same_figures_and_differ_only_in_brief(self):
        from producers import PAIRINGS
        bull, bear = PAIRINGS["bull_bear"]
        self.assertEqual(bull.concepts, bear.concepts)
        self.assertEqual(set(bull.concepts), set(FINANCIAL_LENS.concepts) | set(EARNINGS_LENS.concepts))
        self.assertIn("FOR this company", bull.header)
        self.assertIn("AGAINST this company", bear.header)
        for lens in (bull, bear):
            self.assertIn("using only these figures", lens.header)
        self.assertEqual(PAIRINGS["lenses"], (FINANCIAL_LENS, EARNINGS_LENS))


if __name__ == "__main__":
    unittest.main()
