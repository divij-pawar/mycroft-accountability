"""
B2 (overlapping lens metrics) and B3 (accounting checks, validation/constraints.py),
including how B3's hard failures feed the decision gate (validation/gate.py).

Pinned here:
  - B2: the two lenses share exactly NetIncomeLoss and EarningsPerShareDiluted;
    concept_aware compares figures for shared concepts by value (and the v1
    default, no shared concepts, still excludes them); a shared figure only one
    agent cites is CITED_BY_ONE, not "only one agent was given this".
  - B3: hard rules are definitional and gate; heuristics never gate; checks on the
    filing never gate; a rule over figures from different periods is skipped, not
    failed; an agent rounding or truncating a figure it was given is not misquoting.
  - The gate: a failed hard check is a decision item with its own decision set,
    investor reads withhold its arithmetic, and an existing decisions table from
    before B3 is migrated without losing a row.

No network, no live model.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.directive import DirectiveVersion
from core.parsing import AgentResponse, _parse_response
from producers.earnings import EARNINGS_LENS
from producers.financial import FINANCIAL_LENS
from producers.lens import shared_concepts
from validation import gate as G
from validation.concept_linkage import contradiction_flag_concept_aware
from validation.constraints import run_checks
from validation.facts import contradiction_flag_canonical
from tests.support import no_model_extraction

_AAPL = json.loads((Path(__file__).parent / "fixtures" / "edgar_aapl_companyfacts_sample.json").read_text(encoding="utf-8"))
_GIVEN = {
    "a": [f.to_dict() for f in FINANCIAL_LENS.select(_AAPL) if f],
    "b": [f.to_dict() for f in EARNINGS_LENS.select(_AAPL) if f],
}
SHARED = shared_concepts(FINANCIAL_LENS, EARNINGS_LENS)


def _by_key(result):
    return {c["key"]: c for c in result["checks"]}


# ── B2 ──────────────────────────────────────────────────────────────────────────

class TestOverlappingLenses(unittest.TestCase):

    def test_shared_figures_are_compared_by_value(self):
        flag, divergent, _ = contradiction_flag_concept_aware(
            "Diluted EPS was $2.02.", "Diluted EPS came in at $2.10.", shared_concepts=SHARED)
        self.assertTrue(flag)
        self.assertEqual(divergent, ["$2.02", "$2.10"])

    def test_v1_default_still_excludes_them(self):
        flag, _, _ = contradiction_flag_concept_aware("Diluted EPS was $2.02.", "Diluted EPS came in at $2.10.")
        self.assertFalse(flag)

    def test_rounding_and_one_sided_citation_are_not_conflicts(self):
        flag, _, _ = contradiction_flag_concept_aware(
            "Net income was $29.8 billion.", "Net income reached $29,789,000,000.", shared_concepts=SHARED)
        self.assertFalse(flag)
        flag, _, _ = contradiction_flag_concept_aware(
            "Net income was $29.8 billion.", "Operating income was $35.7 billion.", shared_concepts=SHARED)
        self.assertFalse(flag)

    def test_a_shared_figure_one_agent_skips_is_cited_by_one(self):
        context_a = FINANCIAL_LENS.summarize("AAPL", _AAPL)
        context_b = EARNINGS_LENS.summarize("AAPL", _AAPL)
        _, _, rows = contradiction_flag_canonical(
            "Net income was $29.8 billion and total assets $383.3 billion.",
            "Operating income was $35.7 billion.",
            context_a=context_a, context_b=context_b)
        status = {r.metric: r.status for r in rows}
        self.assertEqual(status["net_income"], "CITED_BY_ONE")      # both were given it
        self.assertEqual(status["total_assets"], "ONE_SIDED")       # only A was given it
        self.assertEqual(status["operating_income"], "ONE_SIDED")   # only B was given it
        self.assertFalse(any(r.flags for r in rows))

    def test_shared_figures_now_match_across_agents(self):
        context_a = FINANCIAL_LENS.summarize("AAPL", _AAPL)
        context_b = EARNINGS_LENS.summarize("AAPL", _AAPL)
        _, _, rows = contradiction_flag_canonical(
            "Diluted EPS was $2.02.", "Diluted EPS of $2.02 for the quarter.", context_a=context_a, context_b=context_b)
        self.assertEqual({r.metric: r.status for r in rows}["eps_diluted"], "MATCH")


# ── B3: rules on an agent's own figures ─────────────────────────────────────────

class TestAgentRules(unittest.TestCase):

    def test_diluted_above_basic_is_a_hard_failure_that_gates(self):
        c = _by_key(run_checks("Basic EPS was $1.60 and diluted EPS $1.64.", None))["check:agent_a:eps_basic_ge_diluted"]
        self.assertEqual((c["outcome"], c["kind"], c["gates"]), ("fail", "hard", True))
        self.assertIn("basic $1.60 vs diluted $1.64", c["math"])

    def test_with_a_loss_equal_eps_passes(self):
        c = _by_key(run_checks("Basic EPS was -$0.40 and diluted EPS -$0.40.", None))
        self.assertEqual(c["check:agent_a:eps_basic_ge_diluted"]["outcome"], "pass")

    def test_free_cash_flow_identity(self):
        bad = _by_key(run_checks(
            "Operating cash flow was $520 million, capital expenditures $140 million, free cash flow $420 million.", None))
        self.assertEqual(bad["check:agent_a:fcf_identity"]["outcome"], "fail")
        self.assertIn("= $380M; stated $420M", bad["check:agent_a:fcf_identity"]["math"])
        good = _by_key(run_checks(
            "Operating cash flow was $520 million, capital expenditures $140 million, free cash flow $380 million.", None))
        self.assertEqual(good["check:agent_a:fcf_identity"]["outcome"], "pass")

    def test_a_heuristic_warns_but_never_gates(self):
        c = _by_key(run_checks("Net income was $50 billion while operating income was $40 billion.", None))
        c = c["check:agent_a:net_le_operating"]
        self.assertEqual((c["outcome"], c["kind"], c["gates"]), ("fail", "heuristic", False))
        self.assertIn("one-off gain", c["reason"])

    def test_figures_from_different_periods_are_skipped_not_failed(self):
        c = _by_key(run_checks("Basic EPS was $1.60 in Q3 FY2026 and diluted EPS $1.64 in Q2 FY2026.", None))
        c = c["check:agent_a:eps_basic_ge_diluted"]
        self.assertEqual(c["outcome"], "skipped")
        self.assertIn("periods", c["reason"])

    def test_a_rule_with_a_missing_figure_is_not_shown(self):
        self.assertEqual(run_checks("Basic EPS was $1.60.", None)["checks"], [])


# ── B3: against what the agent was given ────────────────────────────────────────

class TestClaimsVsSource(unittest.TestCase):

    def _check(self, text, slot="a", metric="revenue"):
        return _by_key(run_checks(text if slot == "a" else None, text if slot == "b" else None,
                                  given_facts=_GIVEN))[f"source:agent_{slot}:{metric}"]

    def test_rounding_or_truncating_is_not_misquoting(self):
        self.assertEqual(self._check("Revenue was $109.4 billion.")["outcome"], "pass")
        self.assertEqual(self._check("Revenue was $109 billion.")["outcome"], "pass")
        self.assertEqual(self._check("Revenue was 109,417,000,000.")["outcome"], "pass")

    def test_a_misquoted_input_is_a_gating_failure_with_the_math(self):
        c = self._check("Revenue was $94.9 billion.")
        self.assertEqual((c["outcome"], c["gates"]), ("fail", True))
        self.assertIn("cited $94.9 billion; given $109.4B (3 months ending 2026-06-27, 10-Q)", c["math"])

    def test_a_prior_period_figure_beside_the_right_one_is_fine(self):
        self.assertEqual(self._check("Revenue rose to $109.4 billion from $94.9 billion.")["outcome"], "pass")

    def test_a_different_stated_period_is_not_checked(self):
        c = self._check("Revenue for fiscal 2025 was $391 billion.")
        self.assertEqual(c["outcome"], "skipped")

    def test_per_share_to_the_cent(self):
        self.assertEqual(self._check("Diluted EPS was $2.02.", "b", "eps_diluted")["outcome"], "pass")
        self.assertEqual(self._check("Diluted EPS was $2.05.", "b", "eps_diluted")["outcome"], "fail")

    def test_only_metrics_the_agent_was_given_are_checked(self):
        keys = _by_key(run_checks("Operating income was $1 billion.", None, given_facts=_GIVEN))
        self.assertNotIn("source:agent_a:operating_income", keys)  # A was never given it


# ── B3: the filing itself ───────────────────────────────────────────────────────

class TestSourceSanity(unittest.TestCase):

    def test_real_filing_passes_and_never_gates(self):
        result = run_checks(None, None, source_payload=_AAPL)
        c = _by_key(result)["check:source:eps_basic_ge_diluted"]
        self.assertEqual(c["outcome"], "pass")
        self.assertIn("(2026-03-29 to 2026-06-27)", c["math"])
        self.assertEqual(result["gating"], [])

    def test_a_filing_that_does_not_add_up_is_reported_not_gated(self):
        bad = {"facts": {"us-gaap": {
            "Assets": {"units": {"USD": [{"end": "2026-06-27", "val": 100.0, "filed": "2026-08-01"}]}},
            "LiabilitiesAndStockholdersEquity": {"units": {"USD": [{"end": "2026-06-27", "val": 90.0, "filed": "2026-08-01"}]}},
        }}}
        c = _by_key(run_checks(None, None, source_payload=bad))["check:source:balance_sheet_filed"]
        self.assertEqual((c["outcome"], c["gates"]), ("fail", False))


# ── Gate integration ────────────────────────────────────────────────────────────

def _payload_with_checks(text_a, text_b="Diluted EPS was $2.02."):
    flags = run_checks(text_a, text_b, given_facts=_GIVEN)
    return {
        "run_id": "run-c", "scope": "auditor", "gate_policy": G.GATE_POLICY, "halted": False,
        "session": {"run_id": "run-c", "initiated_at": "2026-09-25T00:00:00+00:00", "reasoning_objects": []},
        "reasoning_objects": [],
        "cross_agent_comparison": {
            "status": "COMPARED", "agent_a_conclusion": text_a, "agent_b_conclusion": text_b,
            "agent_a_numbers": [], "agent_b_numbers": [], "divergent_numbers": [],
            "metric_comparisons": [{"metric": "revenue", "label": "Revenue", "family": "currency",
                                    "status": "ONE_SIDED", "value_a": 94.9e9, "value_b": None,
                                    "raw_a": "$94.9 billion", "raw_b": None, "period_a": None, "period_b": None,
                                    "variance_pct": None, "note": None, "flags": False}],
            "structural_flags": flags,
        },
    }


class TestChecksGate(unittest.TestCase):
    KEY = "source:agent_a:revenue"
    GOOD = {"decided_by": "Divij", "rationale": "The 10-Q gives $109.4B; A quoted the prior year.",
            "cited_items": [KEY], "final_value": None}

    def test_a_failed_hard_check_opens_the_gate(self):
        g = G.gate_state(_payload_with_checks("Revenue was $94.9 billion."), [])
        self.assertEqual(g["status"], G.AWAITING_DECISION)
        item = next(i for i in g["items"] if i["metric"] == self.KEY)
        self.assertEqual(item["kind"], "check")
        self.assertIn("Agent A", item["label"])

    def test_a_check_takes_its_own_decisions(self):
        p = _payload_with_checks("Revenue was $94.9 billion.")
        with self.assertRaises(G.DecisionError) as refused:
            G.validate_decision(p, {**self.GOOD, "decision": "accept_a"})
        self.assertIn("doesn't apply to a failed check", str(refused.exception))
        out = G.validate_decision(p, {**self.GOOD, "decision": "confirmed_error"})
        self.assertEqual(out["decision"], "confirmed_error")
        g = G.gate_state(p, [{**out, "decision_id": "d1", "decided_at": "t"}])
        self.assertEqual(g["status"], G.DECIDED)

    def test_investors_get_no_arithmetic_for_a_pending_check(self):
        p = _payload_with_checks("Revenue was $94.9 billion.")
        out = G.redact_for_scope(p, "investor", G.gate_state(p, []))
        check = next(c for c in out["cross_agent_comparison"]["structural_flags"]["checks"] if c["key"] == self.KEY)
        self.assertIsNone(check["math"])
        self.assertTrue(check["withheld"])
        row = out["cross_agent_comparison"]["metric_comparisons"][0]
        self.assertTrue(row["withheld"])
        self.assertNotIn("94.9", json.dumps(out))

    def test_passing_checks_do_not_gate(self):
        g = G.gate_state(_payload_with_checks("Revenue was $109.4 billion."), [])
        self.assertEqual(g["status"], G.NO_DECISION_NEEDED)


class _TempDb(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.path = Path(self._tmpdir) / "test.db"
        self._db_patcher = patch("web.db.DB_PATH", self.path)
        self._db_patcher.start()
        from web import db
        self.db = db

    def tearDown(self):
        self._db_patcher.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestDecisionsMigration(_TempDb):
    """A decisions table made by BG (before 'confirmed_error' existed) is rebuilt, rows intact."""

    OLD_TABLE = """
    CREATE TABLE gate_decisions (
        decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
        decided_by TEXT NOT NULL CHECK(length(trim(decided_by)) >= 2),
        decision TEXT NOT NULL CHECK(decision IN ('accept_a','accept_b','both_wrong','not_a_conflict','override_value')),
        final_value REAL, rationale TEXT NOT NULL CHECK(length(trim(rationale)) >= 20),
        cited_items TEXT NOT NULL, decided_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES runs(run_id));
    CREATE TRIGGER gate_decisions_no_delete BEFORE DELETE ON gate_decisions
    BEGIN SELECT RAISE(ABORT, 'append-only'); END;
    """

    def test_rebuilds_once_and_keeps_every_row(self):
        conn = sqlite3.connect(self.path)
        conn.executescript("CREATE TABLE runs (run_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, scope TEXT NOT NULL,"
                           " status TEXT NOT NULL, initiated_at TEXT NOT NULL, completed_at TEXT, confidence REAL,"
                           " payload_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT '2026-09-25T00:00:00Z');"
                           + self.OLD_TABLE)
        conn.execute("INSERT INTO runs (run_id, ticker, scope, status, initiated_at, payload_json)"
                     " VALUES ('r1','X','auditor','COMPLETE','2026-09-25','{}')")
        conn.execute("INSERT INTO gate_decisions VALUES ('d1','r1','Divij','accept_a',NULL,"
                     "'A reason long enough to keep.','[\"revenue\"]','2026-09-25T10:00:00Z')")
        conn.commit()
        conn.close()

        self.db.init_db()
        self.db.init_db()  # a second startup is a no-op
        self.assertEqual([d["decision_id"] for d in self.db.get_decisions("r1")], ["d1"])
        self.db.insert_decision("r1", {"decided_by": "Divij", "decision": "confirmed_error", "final_value": None,
                                       "rationale": "The check is right, A misquoted.", "cited_items": ["x"]})
        with self.db._connect() as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("DELETE FROM gate_decisions")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE gate_decisions SET decided_by = 'Someone'")
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn("gate_decisions_pre_b3", names)


# ── Through the route ───────────────────────────────────────────────────────────

def _agent(conclusion: str):
    def adapter(subject: str, context: str, directive: DirectiveVersion) -> AgentResponse:
        return _parse_response(
            "<thought_log>\n  Read the filing figures I was given.\n</thought_log>\n"
            f"<conclusion>\n  {conclusion}\n</conclusion>")
    return adapter


class TestRoute(_TempDb):

    def setUp(self):
        super().setUp()
        self.db.init_db()
        agents = iter([_agent("Revenue was $94.9 billion and diluted EPS $2.02."),
                       _agent("Diluted EPS was $2.02 and operating income $35.7 billion.")])
        self._patchers = [
            patch("web.server._build_adapter", side_effect=lambda cfg: next(agents)),
            patch("web.server.lookup_cik", return_value="0000320193"),
            patch("web.server.fetch_company_facts", return_value=_AAPL),
            no_model_extraction(),
        ]
        for p in self._patchers:
            p.start()
        from web.server import app
        self.client = TestClient(app)

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        super().tearDown()

    def test_ticker_run_records_lenses_checks_and_gates_on_a_misquote(self):
        tok = self.client.post("/api/auth/token", json={"scope": "auditor"}).json()["access_token"]
        data = self.client.post("/api/compare", json={"ticker": "AAPL"},
                                headers={"Authorization": f"Bearer {tok}"}).json()
        self.assertEqual(data["producers"]["shared_concepts"], ["EarningsPerShareDiluted", "NetIncomeLoss"])
        self.assertEqual(data["producers"]["a"]["lens_version"], "v2")
        cmp = data["cross_agent_comparison"]
        self.assertEqual({r["metric"]: r["status"] for r in cmp["metric_comparisons"]}["eps_diluted"], "MATCH")
        self.assertIn("source:agent_a:revenue", cmp["structural_flags"]["gating"])
        self.assertEqual(data["gate"]["status"], G.AWAITING_DECISION)
        self.assertEqual(data["gate"]["pending"], ["source:agent_a:revenue"])
        stored = self.db.get_run(data["run_id"])
        self.assertEqual(stored["cross_agent_comparison"]["structural_flags"]["policy"], "b3-v1")
        self.assertEqual(stored["gate_policy"], G.GATE_POLICY)


if __name__ == "__main__":
    unittest.main()
