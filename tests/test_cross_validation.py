"""
Cross-Agent Validation tests — SDD v1 §10.

No network, no live model. Producer A's "real" half uses the actual EDGAR-fetching
functions with an injected fetch_fn, exactly as tests/test_financial_grader.py does;
the model call itself is always a fixture or the mock adapter.

The first tests are pure logic against fixtures whose correct answer is known before
the test runs — that is the point of fixture-first validation, not a shortcut.
"""

import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from adapters.fixture_adapter import make_fixture_adapter
from tests.support import make_scripted_adapter
from validation.cross_validation import (
    ComparisonStatus,
    CrossAgentComparisonResult,
    list_contradictions,
    persist_cross_agent_run,
    run_cross_agent_validation,
)
from datasources.edgar import fetch_company_facts, lookup_cik
from producers.earnings import summarize_earnings_facts
from producers.financial import summarize_facts
from core.schemas import AgentID, DataSource, DataSourceStatus, ParseStatus


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _compare(conclusion_a: str, conclusion_b: str, **kwargs):
    """Run a comparison between two fixture conclusions."""
    return run_cross_agent_validation(
        "AAPL",
        "context for the filings agent",
        "context for the earnings agent",
        AgentID.FINANCIAL,
        AgentID.EARNINGS,
        make_fixture_adapter(conclusion_a),
        make_fixture_adapter(conclusion_b),
        **kwargs,
    )


# ─────────────────────────────────────────────
# Core comparison behaviour (SDD §10)
# ─────────────────────────────────────────────

class TestAgreement(unittest.TestCase):

    def test_matching_number_different_wording_is_not_a_contradiction(self):
        result, _ = _compare(
            "Revenue grew 12% year over year.",
            "Revenue increased approximately 12%.",
        )
        self.assertEqual(result.status, ComparisonStatus.COMPARED)
        self.assertFalse(result.contradiction_flag)
        self.assertEqual(result.divergent_numbers, [])

    def test_identical_conclusions_score_perfectly(self):
        text = "Revenue grew 12%."
        result, _ = _compare(text, text)
        self.assertEqual(result.score, 1.0)
        self.assertEqual(result.word_overlap, 1.0)
        self.assertEqual(result.number_overlap, 1.0)
        self.assertEqual(result.agreement, "HIGH")
        self.assertFalse(result.contradiction_flag)

    def test_no_numbers_on_either_side_is_not_a_contradiction(self):
        # Mirrors validation/consistency.py's existing rule: absence of numbers on both sides
        # is not penalised, because there is nothing quantitative to disagree about.
        result, _ = _compare(
            "Revenue grew modestly on strong demand.",
            "Revenue increased somewhat this period.",
        )
        self.assertEqual(result.status, ComparisonStatus.COMPARED)
        self.assertFalse(result.contradiction_flag)
        self.assertEqual(result.divergent_numbers, [])


class TestContradiction(unittest.TestCase):

    def test_different_values_for_the_same_metric_are_flagged(self):
        result, _ = _compare(
            "Revenue grew 12% year over year, per the 10-K.",
            "On the call, the CFO cited 8% revenue growth.",
        )
        self.assertEqual(result.status, ComparisonStatus.COMPARED)
        self.assertTrue(result.contradiction_flag)
        self.assertEqual(result.divergent_numbers, ["12%", "8%"])

    def test_number_present_in_one_and_absent_in_the_other_is_flagged(self):
        # SDD §7 definition-of-done edge case: absent, not merely different.
        # Handled by the same symmetric-difference rule, with no special-casing.
        result, _ = _compare(
            "Revenue grew 12%.",
            "Revenue grew, driven by strong demand.",
        )
        self.assertEqual(result.status, ComparisonStatus.COMPARED)
        self.assertTrue(result.contradiction_flag)
        self.assertEqual(result.divergent_numbers, ["12%"])

    def test_dollar_amounts_diverge(self):
        result, _ = _compare(
            "Assets total $365 billion.",
            "Assets total $350 billion.",
        )
        self.assertTrue(result.contradiction_flag)
        self.assertEqual(len(result.divergent_numbers), 2)

    def test_bare_decimal_eps_values_diverge(self):
        # Real live-run case (GOOGL, 2026-08-29): EPS figures with no unit suffix were
        # previously invisible to _extract_numbers and silently excluded from comparison.
        # See divij/model-test-report-2026-08-29.md, Test 5.
        result, _ = _compare(
            "Diluted EPS was 14.41.",
            "Diluted EPS was 14.24.",
        )
        self.assertTrue(result.contradiction_flag)
        self.assertEqual(result.divergent_numbers, ["14.24", "14.41"])


# ─────────────────────────────────────────────
# Halt handling (SDD §9) — a halt is evidence, not a discard
# ─────────────────────────────────────────────

class TestHaltPaths(unittest.TestCase):

    def test_agent_a_halt_preserves_both_agents_records(self):
        result, objects = run_cross_agent_validation(
            "AAPL", "ctx a", "ctx b",
            AgentID.FINANCIAL, AgentID.EARNINGS,
            make_scripted_adapter("halt"),
            make_fixture_adapter("Revenue grew 12%."),
        )
        self.assertEqual(result.status, ComparisonStatus.AGENT_A_HALTED)
        # Agent A: attempt 1 PARSE_FAILURE + attempt 2 HALT. Agent B: attempt 1 SUCCESS.
        self.assertEqual(len(objects), 3)
        statuses = [o.parse_status for o in objects]
        self.assertIn(ParseStatus.HALT, statuses)
        self.assertIn(ParseStatus.SUCCESS, statuses)
        # Agent B's evidence survives agent A's failure.
        self.assertIsNotNone(result.agent_b_conclusion)
        self.assertIsNone(result.agent_a_conclusion)

    def test_agent_b_halt_is_reported_distinctly(self):
        result, objects = run_cross_agent_validation(
            "AAPL", "ctx a", "ctx b",
            AgentID.FINANCIAL, AgentID.EARNINGS,
            make_fixture_adapter("Revenue grew 12%."),
            make_scripted_adapter("halt"),
        )
        self.assertEqual(result.status, ComparisonStatus.AGENT_B_HALTED)
        self.assertEqual(len(objects), 3)

    def test_both_halted(self):
        result, objects = run_cross_agent_validation(
            "AAPL", "ctx a", "ctx b",
            AgentID.FINANCIAL, AgentID.EARNINGS,
            make_scripted_adapter("halt"),
            make_scripted_adapter("halt"),
        )
        self.assertEqual(result.status, ComparisonStatus.BOTH_HALTED)
        self.assertEqual(len(objects), 4)

    def test_contradiction_flag_is_none_not_false_when_no_comparison_happened(self):
        # P3: reporting False would claim "checked, found nothing", which is a
        # stronger statement than "no comparison was possible".
        result, _ = run_cross_agent_validation(
            "AAPL", "ctx a", "ctx b",
            AgentID.FINANCIAL, AgentID.EARNINGS,
            make_scripted_adapter("halt"),
            make_fixture_adapter("Revenue grew 12%."),
        )
        self.assertIsNone(result.contradiction_flag)
        self.assertIsNone(result.score)
        self.assertIsNone(result.agreement)
        self.assertIsNone(result.word_overlap)
        self.assertIsNone(result.number_overlap)

    def test_retry_then_success_is_compared_normally(self):
        # ADR-07 recovery: agent A fails attempt 1, succeeds on the corrective retry.
        # The comparison should proceed on the successful conclusion.
        result, objects = run_cross_agent_validation(
            "AAPL", "ctx a", "ctx b",
            AgentID.FINANCIAL, AgentID.EARNINGS,
            make_scripted_adapter("retry_success"),
            make_fixture_adapter("Revenue grew 12%."),
        )
        self.assertEqual(result.status, ComparisonStatus.COMPARED)
        self.assertEqual(len(objects), 3)  # A: failure + success, B: success
        self.assertIsNotNone(result.contradiction_flag)


# ─────────────────────────────────────────────
# concepts_expected_to_overlap — item 2 of divij/model-test-report-2026-08-29.md's
# suggested next steps. Default (True) preserves every test above unchanged.
# ─────────────────────────────────────────────

class TestConceptsExpectedToOverlap(unittest.TestCase):

    def test_default_still_flags_presence_absence(self):
        # Regression: the default must match every pre-existing test in this file.
        result, _ = _compare("Revenue grew 12%.", "Revenue grew, driven by strong demand.")
        self.assertTrue(result.contradiction_flag)

    def test_false_suppresses_pure_presence_absence(self):
        # The AAPL/JPM live-run shape: one side quantifies, the other simply wasn't
        # asked about that concept. Not evidence of disagreement on its own.
        result, _ = _compare(
            "Revenue grew 12%.",
            "Revenue grew, driven by strong demand.",
            concepts_expected_to_overlap=False,
        )
        self.assertFalse(result.contradiction_flag)
        # Nothing is hidden — divergent_numbers is still reported in full.
        self.assertEqual(result.divergent_numbers, ["12%"])

    def test_false_still_flags_genuine_value_conflict(self):
        # Both sides quantitative, genuinely conflicting values — still a contradiction
        # even in asymmetric mode.
        result, _ = _compare(
            "Revenue grew 12%.",
            "Revenue grew 8%.",
            concepts_expected_to_overlap=False,
        )
        self.assertTrue(result.contradiction_flag)

    def test_false_does_not_fix_disjoint_concept_sets(self):
        # Documented, known remaining gap: two non-empty sets about genuinely
        # different concepts (the TSLA live-run shape — assets/revenue vs. EPS) still
        # get flagged, since there's no concept linkage to tell "different values for
        # the same thing" apart from "different things". This test exists so that gap
        # stays a known, asserted limitation rather than a silent regression surface.
        result, _ = _compare(
            "Assets total $365 billion.",
            "Diluted EPS was 6.45.",
            concepts_expected_to_overlap=False,
        )
        self.assertTrue(result.contradiction_flag)

    def test_false_both_empty_still_not_flagged(self):
        result, _ = _compare(
            "Revenue grew modestly on strong demand.",
            "Revenue increased somewhat this period.",
            concepts_expected_to_overlap=False,
        )
        self.assertFalse(result.contradiction_flag)


class TestContradictionRuleConceptAware(unittest.TestCase):
    """
    contradiction_rule="concept_aware" delegates to validation/concept_linkage.py.
    2026-09-11: wired in as an opt-in alternative to symmetric_difference,
    which stays the default so every test above this class is unaffected.
    """

    def test_disjoint_known_concepts_are_not_flagged(self):
        # The exact case TestConceptsExpectedToOverlap.test_false_does_not_fix_
        # disjoint_concept_sets documents as an unfixed gap under
        # symmetric_difference — concept_aware closes it, because both numbers
        # tag to a known concept (Assets, EarningsPerShareDiluted) that can
        # never be corroborated by the other side.
        result, _ = _compare(
            "Assets total $365 billion.",
            "Diluted EPS was 6.45.",
            contradiction_rule="concept_aware",
        )
        self.assertFalse(result.contradiction_flag)
        self.assertEqual(result.divergent_numbers, [])

    def test_untagged_numeric_conflict_is_still_flagged(self):
        # A number with no known concept nearby keeps the old presence/absence
        # rule — concept_aware narrows the false-positive surface, it doesn't
        # turn contradiction detection off.
        result, _ = _compare(
            "The debt-to-equity ratio is 0.34.",
            "The debt-to-equity ratio is 0.51.",
            contradiction_rule="concept_aware",
        )
        self.assertTrue(result.contradiction_flag)
        self.assertEqual(result.divergent_numbers, ["0.34", "0.51"])

    def test_fabrication_with_empty_other_side_is_still_flagged(self):
        # The historic AAPL case (divij/cross-agent-validation-disjoint-concepts-
        # diagnosis.md §3a): symmetric_difference with
        # concepts_expected_to_overlap=False requires both sides non-empty and
        # so suppresses this. concept_aware never imposed that gate.
        result, _ = _compare(
            "The debt-to-equity ratio of 0.34 indicates moderate leverage.",
            "Earnings quality appears strong with consistent EPS figures.",
            contradiction_rule="concept_aware",
        )
        self.assertTrue(result.contradiction_flag)
        self.assertIn("0.34", result.divergent_numbers)

    def test_no_numbers_on_either_side_is_not_flagged(self):
        result, _ = _compare(
            "Revenue grew modestly on strong demand.",
            "Revenue increased somewhat this period.",
            contradiction_rule="concept_aware",
        )
        self.assertFalse(result.contradiction_flag)
        self.assertEqual(result.divergent_numbers, [])


# ─────────────────────────────────────────────
# Shared run_id — the accountability-layer integration invariant
# ─────────────────────────────────────────────

class TestSharedRunId(unittest.TestCase):

    def test_both_agents_records_share_one_run_id(self):
        # Unlike validation/consistency.py's probe (fresh UUID, never persisted), both agents
        # here belong to the same run because the comparison is the evidence.
        result, objects = _compare("Revenue grew 12%.", "Revenue grew 8%.")
        self.assertTrue(all(o.run_id == result.run_id for o in objects))

    def test_caller_supplied_run_id_is_honoured(self):
        run_id = uuid.uuid4()
        result, objects = _compare("Revenue grew 12%.", "Revenue grew 12%.", run_id=run_id)
        self.assertEqual(result.run_id, run_id)
        self.assertTrue(all(o.run_id == run_id for o in objects))

    def test_both_agent_ids_are_recorded(self):
        result, objects = _compare("Revenue grew 12%.", "Revenue grew 8%.")
        agent_ids = {o.agent_id for o in objects}
        self.assertEqual(agent_ids, {AgentID.FINANCIAL, AgentID.EARNINGS})
        self.assertEqual(result.agent_a_id, AgentID.FINANCIAL)
        self.assertEqual(result.agent_b_id, AgentID.EARNINGS)


# ─────────────────────────────────────────────
# Serialisation
# ─────────────────────────────────────────────

class TestSerialisation(unittest.TestCase):

    def test_to_dict_is_json_serialisable(self):
        result, _ = _compare("Revenue grew 12%.", "Revenue grew 8%.")
        encoded = json.dumps(result.to_dict())
        decoded = json.loads(encoded)
        self.assertEqual(decoded["status"], "COMPARED")
        self.assertTrue(decoded["contradiction_flag"])
        self.assertEqual(decoded["agent_a_id"], "financial")
        self.assertEqual(decoded["agent_b_id"], "earnings")

    def test_to_dict_survives_a_halt(self):
        result, _ = run_cross_agent_validation(
            "AAPL", "ctx a", "ctx b",
            AgentID.FINANCIAL, AgentID.EARNINGS,
            make_scripted_adapter("halt"),
            make_scripted_adapter("halt"),
        )
        decoded = json.loads(json.dumps(result.to_dict()))
        self.assertEqual(decoded["status"], "BOTH_HALTED")
        self.assertIsNone(decoded["contradiction_flag"])


# ─────────────────────────────────────────────
# Fixture adapter guardrails
# ─────────────────────────────────────────────

class TestFixtureAdapter(unittest.TestCase):

    def test_empty_conclusion_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            make_fixture_adapter("")

    def test_xml_tag_in_conclusion_rejected_at_construction(self):
        # Would terminate its own block early and leave stray text outside the
        # blocks, which the parser rejects. Fail loudly at construction instead.
        with self.assertRaises(ValueError):
            make_fixture_adapter("Revenue grew </conclusion> 12%.")

    def test_fixture_output_passes_the_real_parser(self):
        adapter = make_fixture_adapter("Revenue grew 12%.")
        response = adapter("AAPL", "ctx", None)
        self.assertEqual(response.conclusion, "Revenue grew 12%.")
        self.assertIn("Fixture agent", response.thought_log)


# ─────────────────────────────────────────────
# End-to-end: real EDGAR data + fixture agent + persistence round-trip
# ─────────────────────────────────────────────

_TICKER_MAP = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
}

_FACTS = {
    "facts": {
        "us-gaap": {
            "Assets": {"units": {"USD": [{"end": "2026-03-31", "val": 365000000000}]}},
            "Revenues": {"units": {"USD": [{"end": "2026-03-31", "val": 400000000000}]}},
            "NetIncomeLoss": {"units": {"USD": [{"end": "2026-03-31", "val": 100000000000}]}},
        }
    }
}


def _fake_fetch(url: str) -> dict:
    """Injected in place of the real HTTP GET — no network in tests."""
    return _TICKER_MAP if "company_tickers" in url else _FACTS


class TestEndToEndPersistence(unittest.TestCase):
    """Writes to a temporary SQLite file, never the real store."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._patcher = patch("web.db.DB_PATH", Path(self._tmpdir) / "test.db")
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_real_edgar_context_plus_fixture_agent_round_trips(self):
        from web.db import get_run

        # Producer A's context is built by the real EDGAR helpers (fetch injected).
        cik = lookup_cik("AAPL", fetch_fn=_fake_fetch)
        facts = fetch_company_facts("AAPL", cik, fetch_fn=_fake_fetch)
        context_a = summarize_facts("AAPL", facts)
        self.assertIn("365000000000", context_a)

        edgar_source = DataSource(
            source="SEC EDGAR",
            status=DataSourceStatus.SIMULATED,  # injected fetch, not a live call
            url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            provenance_note="Injected fixture fetch — no network call performed.",
        )

        result, objects = run_cross_agent_validation(
            "AAPL",
            context_a,
            "Earnings call transcript summary for AAPL.",
            AgentID.FINANCIAL,
            AgentID.EARNINGS,
            make_fixture_adapter("Assets total $365 billion; revenue grew 12%."),
            make_fixture_adapter("Management cited 8% revenue growth on the call."),
            data_sources_a=(edgar_source,),
        )

        self.assertEqual(result.status, ComparisonStatus.COMPARED)
        self.assertTrue(result.contradiction_flag)

        stored = persist_cross_agent_run(result, objects)
        self.assertEqual(stored["cross_agent_comparison"]["status"], "COMPARED")

        # The point of the integration: read it back out of the append-only store.
        retrieved = get_run(str(result.run_id))
        self.assertIsNotNone(retrieved)
        comparison = retrieved["cross_agent_comparison"]
        self.assertTrue(comparison["contradiction_flag"])
        self.assertEqual(comparison["agent_a_id"], "financial")
        self.assertEqual(comparison["agent_b_id"], "earnings")
        self.assertIn("12%", comparison["divergent_numbers"])
        self.assertIn("8%", comparison["divergent_numbers"])

        # Both agents' full attempt history persisted under the one run.
        self.assertEqual(len(retrieved["reasoning_objects"]), 2)
        self.assertEqual(len(retrieved["session"]["reasoning_objects"]), 2)
        # Provenance survived the round trip.
        self.assertEqual(
            retrieved["reasoning_objects"][0]["data_sources"][0]["source"], "SEC EDGAR"
        )

    def test_halted_run_is_persisted_as_halted(self):
        from web.db import get_run

        result, objects = run_cross_agent_validation(
            "AAPL", "ctx a", "ctx b",
            AgentID.FINANCIAL, AgentID.EARNINGS,
            make_scripted_adapter("halt"),
            make_fixture_adapter("Revenue grew 12%."),
        )
        persist_cross_agent_run(result, objects)

        retrieved = get_run(str(result.run_id))
        self.assertTrue(retrieved["halted"])
        self.assertEqual(retrieved["session"]["status"], "HALTED")
        self.assertEqual(
            retrieved["cross_agent_comparison"]["status"], "AGENT_A_HALTED"
        )
        self.assertIsNone(retrieved["cross_agent_comparison"]["contradiction_flag"])
        # The failed attempts are in the record, not discarded.
        self.assertEqual(len(retrieved["reasoning_objects"]), 3)


# ─────────────────────────────────────────────
# Real + real, end-to-end — Producer B is no longer only a fixture for the *data*
# half of the comparison (SDD §10's "Real + fixture, end-to-end" row upgraded per
# the proposal's §6.4 stretch goal). Both producers' contexts come from
# producers/financial.py / producers/earnings.py's real EDGAR-fetching logic, reading different
# concepts from the same companyfacts payload. The LLM call itself remains a
# fixture here, per this suite's "no live model calls in the automated tests"
# convention — see scripts/run_cross_agent_live.py for the manual script that runs both
# producers through a real model.
# ─────────────────────────────────────────────

_COMBINED_FACTS = {
    "facts": {
        "us-gaap": {
            "Assets": {"units": {"USD": [{"end": "2026-03-31", "val": 365000000000}]}},
            "Revenues": {"units": {"USD": [{"end": "2026-03-31", "val": 400000000000}]}},
            "NetIncomeLoss": {"units": {"USD": [{"end": "2026-03-31", "val": 100000000000}]}},
            "EarningsPerShareDiluted": {
                "units": {"USD/shares": [{"end": "2026-03-31", "val": 6.45}]}
            },
            "OperatingIncomeLoss": {"units": {"USD": [{"end": "2026-03-31", "val": 120000000000}]}},
        }
    }
}


def _fake_combined_fetch(url: str) -> dict:
    return _TICKER_MAP if "company_tickers" in url else _COMBINED_FACTS


class TestRealVersusRealEndToEnd(unittest.TestCase):
    """Both producers built from their real grader logic, not a fixture producer."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._patcher = patch("web.db.DB_PATH", Path(self._tmpdir) / "test.db")
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_two_real_graders_same_company_different_concepts(self):
        from web.db import get_run

        cik = lookup_cik("AAPL", fetch_fn=_fake_combined_fetch)
        facts = fetch_company_facts("AAPL", cik, fetch_fn=_fake_combined_fetch)
        context_a = summarize_facts("AAPL", facts)
        context_b = summarize_earnings_facts("AAPL", facts)

        # Same company, same underlying payload, different evidence — except the two
        # figures lens v2 (B2, 2026-09-25) deliberately gives both producers.
        self.assertIn("Assets:", context_a)
        self.assertIn("EarningsPerShareDiluted:", context_b)
        self.assertIn("EarningsPerShareDiluted:", context_a)
        self.assertNotIn("Assets:", context_b)
        self.assertNotIn("OperatingIncomeLoss", context_a)

        edgar_source = DataSource(
            source="SEC EDGAR",
            status=DataSourceStatus.SIMULATED,  # injected fetch, not a live call
            url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            provenance_note="Injected fixture fetch — no network call performed.",
        )

        result, objects = run_cross_agent_validation(
            "AAPL",
            context_a,
            context_b,
            AgentID.FINANCIAL,
            AgentID.EARNINGS,
            make_fixture_adapter("Diluted EPS came in at $6.45, beating expectations."),
            make_fixture_adapter("The call cited diluted EPS of $6.20 for the quarter."),
            data_sources_a=(edgar_source,),
            data_sources_b=(edgar_source,),
        )

        self.assertEqual(result.status, ComparisonStatus.COMPARED)
        self.assertTrue(result.contradiction_flag)
        self.assertEqual(result.divergent_numbers, ["$6.20", "$6.45"])

        persist_cross_agent_run(result, objects)
        retrieved = get_run(str(result.run_id))
        self.assertIsNotNone(retrieved)
        comparison = retrieved["cross_agent_comparison"]
        self.assertEqual(comparison["agent_a_id"], "financial")
        self.assertEqual(comparison["agent_b_id"], "earnings")
        self.assertTrue(comparison["contradiction_flag"])
        # Both producers' real-fetch provenance survived the round trip.
        for ro in retrieved["reasoning_objects"]:
            self.assertEqual(ro["data_sources"][0]["source"], "SEC EDGAR")

    def test_agreeing_real_graders_no_contradiction(self):
        cik = lookup_cik("AAPL", fetch_fn=_fake_combined_fetch)
        facts = fetch_company_facts("AAPL", cik, fetch_fn=_fake_combined_fetch)
        context_a = summarize_facts("AAPL", facts)
        context_b = summarize_earnings_facts("AAPL", facts)

        result, _ = run_cross_agent_validation(
            "AAPL",
            context_a,
            context_b,
            AgentID.FINANCIAL,
            AgentID.EARNINGS,
            make_fixture_adapter("Diluted EPS came in at $6.45 for the quarter."),
            make_fixture_adapter("The call confirmed diluted EPS of approximately $6.45."),
        )

        self.assertEqual(result.status, ComparisonStatus.COMPARED)
        self.assertFalse(result.contradiction_flag)


# ─────────────────────────────────────────────
# Same-source agreement — the generic (non-financial) case: both agents are
# handed the IDENTICAL context, so any numeric disagreement between their
# conclusions is inherently suspicious (concepts_expected_to_overlap=True,
# the default). Uses AgentID.GENERIC_A / GENERIC_B — the domain-neutral pair
# web/server.py's /api/compare picks for a non-ticker subject — rather than
# FINANCIAL/EARNINGS, which are *deliberately* given different context on
# purpose (see TestRealVersusRealEndToEnd above) and would be the wrong pair
# to demonstrate a same-source scenario with.
# ─────────────────────────────────────────────

class TestSameSourceAgreement(unittest.TestCase):

    SOURCE = "Company X reported quarterly revenue of $4.20 million, per the press release."

    def _compare_generic(self, conclusion_a: str, conclusion_b: str):
        return run_cross_agent_validation(
            "Company X quarterly revenue",
            self.SOURCE,
            self.SOURCE,  # both agents see the identical source — the point of this test
            AgentID.GENERIC_A,
            AgentID.GENERIC_B,
            make_fixture_adapter(conclusion_a),
            make_fixture_adapter(conclusion_b),
        )

    def test_same_source_matching_conclusions_is_not_a_contradiction(self):
        result, _ = self._compare_generic(
            "Revenue for the quarter was $4.20 million.",
            "Quarterly revenue came in at $4.20 million.",
        )
        self.assertEqual(result.status, ComparisonStatus.COMPARED)
        self.assertFalse(result.contradiction_flag)
        self.assertEqual(result.divergent_numbers, [])
        self.assertEqual(result.agreement, "HIGH")

    def test_same_source_diverging_conclusions_is_flagged(self):
        # Same source given to both — an actual numeric disagreement here is
        # exactly what this comparison exists to catch: with identical
        # evidence, one side citing a different figure is a hallucination
        # candidate, not a legitimate difference of emphasis.
        result, _ = self._compare_generic(
            "Revenue for the quarter was $4.20 million.",
            "Revenue for the quarter was $5.10 million.",
        )
        self.assertEqual(result.status, ComparisonStatus.COMPARED)
        self.assertTrue(result.contradiction_flag)
        self.assertEqual(result.divergent_numbers, ["$4.20 million", "$5.10 million"])

    def test_same_source_disagreement_on_a_bare_year(self):
        # Written 2026-09-24 as a pinned gap, flipped the same day by B1. The old
        # rule still can't see it: core/numeric.py's QUANTITATIVE_RE has no
        # alternative for a bare integer, so under symmetric_difference two agents
        # disagreeing on a release year read as "no numbers on either side".
        result, _ = self._compare_generic(
            "The film was released in 2014.",
            "The film was released in 2015.",
        )
        self.assertEqual(result.agent_a_numbers, [])
        self.assertFalse(result.contradiction_flag)

        # canonical_facts with years — what /api/compare's generic mode now uses —
        # compares the two release years and flags the disagreement.
        result, _ = run_cross_agent_validation(
            "Company X quarterly revenue", self.SOURCE, self.SOURCE,
            AgentID.GENERIC_A, AgentID.GENERIC_B,
            make_fixture_adapter("The film was released in 2014."),
            make_fixture_adapter("The film was released in 2015."),
            contradiction_rule="canonical_facts", include_years=True,
        )
        self.assertTrue(result.contradiction_flag)
        row = result.metric_comparisons[0]
        self.assertEqual((row["label"], row["status"], row["raw_a"], row["raw_b"]),
                         ("Release year", "MISMATCH", "2014", "2015"))


# ─────────────────────────────────────────────
# list_contradictions — closes SDD §7.3's "not independently queryable" gap
# ─────────────────────────────────────────────

class TestListContradictions(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._patcher = patch("web.db.DB_PATH", Path(self._tmpdir) / "test.db")
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _persist(self, conclusion_a: str, conclusion_b: str) -> uuid.UUID:
        result, objects = _compare(conclusion_a, conclusion_b)
        persist_cross_agent_run(result, objects)
        return result.run_id

    def test_flagged_run_is_returned(self):
        run_id = self._persist("Revenue grew 12%.", "Revenue grew 8%.")
        flagged = list_contradictions()
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["run_id"], str(run_id))

    def test_agreeing_run_is_not_returned(self):
        self._persist("Revenue grew 12%.", "Revenue grew 12%.")
        self.assertEqual(list_contradictions(), [])

    def test_halted_run_is_not_returned(self):
        # contradiction_flag is None on a halt, not True — must not be mistaken
        # for a flagged contradiction (P3: None means "no comparison possible",
        # not "checked and found nothing", and neither is a flag).
        result, objects = run_cross_agent_validation(
            "AAPL", "ctx a", "ctx b",
            AgentID.FINANCIAL, AgentID.EARNINGS,
            make_scripted_adapter("halt"),
            make_fixture_adapter("Revenue grew 12%."),
        )
        persist_cross_agent_run(result, objects)
        self.assertEqual(list_contradictions(), [])

    def test_filters_by_ticker(self):
        self._persist("Revenue grew 12%.", "Revenue grew 8%.")
        self.assertEqual(len(list_contradictions(ticker="AAPL")), 1)
        self.assertEqual(list_contradictions(ticker="MSFT"), [])

    def test_mixed_runs_only_flagged_ones_returned(self):
        flagged_id = self._persist("Revenue grew 12%.", "Revenue grew 8%.")
        self._persist("Revenue grew 12%.", "Revenue grew 12%.")
        flagged = list_contradictions()
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["run_id"], str(flagged_id))


if __name__ == "__main__":
    unittest.main()
