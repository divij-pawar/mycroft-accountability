"""
B4 option 1 (the assessment extraction call) and B5 (divergence classification,
counted evidence, grade candidates, consensus, and the set-the-grade gate item).

Pinned here:
  - the extraction reads the finished answer and adds nothing: a key point quoting
    a figure the agent never wrote is dropped, an assumption it never stated is
    dropped, an honest "no view" is kept as an abstention, a failed call is recorded
    and never fails the run;
  - the divergence class is deterministic: data before assumption before weighting;
  - a consensus grade exists only when both agents agree on grade AND direction and
    no hard check failed;
  - differing grades gate the run (policy v3); a human's grade is the only grade an
    investor ever sees.
No network, no live model (tests/__init__.py makes a real model call impossible).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.assessment import EXTRACTION_PROMPT_VERSION, extract_assessment
from core.parsing import _parse_response
from core.schemas import AgentID, ParseStatus
from pipeline.middleware import HaltError, run_validation_loop
from validation import gate as G
from validation.divergence import synthesize

ANSWER = "Revenue was $109.4 billion and net income $29.8 billion; the company looks solid, a hold."
CONTEXT = "Revenues: 109417000000.0 USD\nNetIncomeLoss: 29789000000.0 USD"
TWO = f"<thought_log>\n  Read the Context.\n</thought_log>\n<conclusion>\n  {ANSWER}\n</conclusion>"


def reply(obj) -> "callable":
    return lambda system, user: obj if isinstance(obj, str) else json.dumps(obj)


class TestTestPackageIsInert(unittest.TestCase):

    def test_importing_the_tests_changes_no_production_code(self):
        # web/self_report.py imports this package in the running server to count
        # tests; a module-level patch here once switched real model calls off there.
        import importlib
        import adapters.langchain_adapter as lc
        importlib.import_module("tests")
        importlib.import_module("tests.support")
        self.assertEqual(lc._build_chat.__module__, "adapters.langchain_adapter")
        self.assertEqual(lc._build_chat.__name__, "_build_chat")


class TestExtraction(unittest.TestCase):

    def _x(self, obj, conclusion=ANSWER):
        return extract_assessment(reply(obj), "AAPL", conclusion, None, CONTEXT)

    def test_a_faithful_reply_is_valid(self):
        p = self._x({"grade": "A", "direction": "hold", "key_points": ["Revenue was $109.4 billion."]})
        self.assertEqual((p.status, p.assessment["grade"]), ("valid", "A"))

    def test_a_key_point_the_agent_never_wrote_is_dropped(self):
        p = self._x({"grade": "A", "direction": "hold",
                     "key_points": ["Operating income was $6.373 billion.", "Revenue was $109.4 billion."]})
        self.assertEqual(p.assessment["key_points"], ["Revenue was $109.4 billion."])
        self.assertTrue(any("$6.373 billion" in i for i in p.issues))
        self.assertEqual(p.status, "partial")

    def test_an_assumption_the_agent_never_stated_is_dropped(self):
        p = self._x({"grade": "A", "direction": "hold",
                     "assumptions": {"revenue_growth_pct": 8, "margin_trend": "stable"}})
        self.assertEqual(p.assessment["assumptions"], {"margin_trend": "stable"})
        self.assertTrue(any("revenue_growth_pct=8" in i for i in p.issues))
        stated = self._x({"grade": "A", "direction": "hold", "assumptions": {"revenue_growth_pct": 8}},
                         conclusion=ANSWER + " I expect 8% revenue growth.")
        self.assertEqual(stated.assessment["assumptions"], {"revenue_growth_pct": 8})

    def test_a_past_growth_rate_is_not_an_assumption(self):
        # Live, 2026-09-26 (extract v1): "revenue up 16% year over year" came back as
        # revenue_growth_pct=16 and B5 read two past figures as differing assumptions.
        past = self._x({"grade": "A", "direction": "buy", "assumptions": {"revenue_growth_pct": 16}},
                       conclusion="Revenue was $109.4 billion, up 16% year over year.")
        self.assertNotIn("assumptions", past.assessment)
        self.assertTrue(any("past result" in i for i in past.issues))
        guided = self._x({"grade": "A", "direction": "buy", "assumptions": {"revenue_growth_pct": 70}},
                         conclusion="It grew 106% last year and guidance calls for about 70% growth in fiscal 2028.")
        self.assertEqual(guided.assessment["assumptions"], {"revenue_growth_pct": 70})

    def test_an_honest_no_view_is_an_abstention(self):
        p = self._x({"abstain": True, "reason": "The answer only lists figures."})
        self.assertEqual((p.status, p.assessment, p.issues), ("abstained", None, ["The answer only lists figures."]))

    def test_a_fenced_reply_is_read_and_the_wrapping_reported(self):
        p = self._x('Here you go:\n```json\n{"grade": "BBB", "direction": "sell"}\n```')
        self.assertEqual((p.status, p.assessment["grade"]), ("partial", "BBB"))
        self.assertIn("text around its JSON", p.issues[0])

    def test_a_failed_call_is_recorded_not_raised(self):
        def boom(system, user):
            raise ConnectionError("model unreachable")
        p = extract_assessment(boom, "AAPL", ANSWER, None, CONTEXT)
        self.assertEqual(p.status, "extraction_failed")
        self.assertIn("model unreachable", p.issues[0])


def _agent(first: str, retry: str | None = None):
    calls = {"n": 0}

    def adapter(subject, context, directive):
        calls["n"] += 1
        return _parse_response(first if calls["n"] == 1 else retry)
    return adapter


class TestRecording(unittest.TestCase):

    def _run(self, adapter, assess):
        return run_validation_loop("AAPL", CONTEXT, uuid.uuid4(), AgentID.FINANCIAL,
                                   call_agent_fn=adapter, assess_fn=assess)

    def test_the_successful_answer_gets_an_extracted_assessment_with_its_provenance(self):
        ro = self._run(_agent(TWO), reply({"grade": "A", "direction": "hold"})).reasoning_objects[0]
        self.assertEqual((ro.assessment_status, ro.assessment_source), ("valid", EXTRACTION_PROMPT_VERSION))
        self.assertEqual(ro.raw_output["assessment_extraction"]["prompt_version"], EXTRACTION_PROMPT_VERSION)
        self.assertIn('"grade": "A"', ro.raw_output["assessment_extraction"]["reply"])
        self.assertEqual(ro.raw_output["text"], TWO)  # the agent's own output is untouched

    def test_a_failed_extraction_never_fails_the_answer(self):
        def boom(system, user):
            raise ConnectionError("down")
        ro = self._run(_agent(TWO), boom).reasoning_objects[0]
        self.assertEqual((ro.parse_status, ro.assessment_status), (ParseStatus.SUCCESS, "extraction_failed"))

    def test_the_retry_answer_is_extracted_too_and_a_halt_gets_none(self):
        result = self._run(_agent("no blocks", TWO), reply({"grade": "B", "direction": "sell"}))
        self.assertEqual(result.reasoning_objects[1].assessment["grade"], "B")
        self.assertIsNone(result.reasoning_objects[0].assessment_status)
        with self.assertRaises(HaltError) as halted:
            self._run(_agent("no", "still no"), reply({"grade": "B", "direction": "sell"}))
        self.assertTrue(all(o.assessment_status is None for o in halted.exception.reasoning_objects))


def _row(metric, status, a=1.0, b=1.0):
    return {"metric": metric, "label": metric.replace("_", " ").title(), "status": status,
            "raw_a": None if a is None else str(a), "raw_b": None if b is None else str(b)}


CHECKS = {"checks": [
    {"key": "source:agent_a:revenue", "rule": "matches_source", "kind": "hard", "scope": "agent_a",
     "outcome": "pass", "metrics": ["revenue"]},
    {"key": "source:agent_b:revenue", "rule": "matches_source", "kind": "hard", "scope": "agent_b",
     "outcome": "fail", "metrics": ["revenue"]},
]}


class TestSynthesis(unittest.TestCase):

    def test_data_conflicts_come_first(self):
        s = synthesize([_row("revenue", "MISMATCH")], CHECKS, {"a": {"grade": "A", "direction": "buy"},
                                                                "b": {"grade": "B", "direction": "sell"}})
        self.assertEqual((s["primary_conflict_driver"], s["data_conflicts"]), ("data", ["revenue"]))
        self.assertTrue(s["needs_decision"])
        self.assertIsNone(s["consensus_grade"])

    def test_same_figures_different_assumptions(self):
        s = synthesize([_row("revenue", "MATCH")], None, {
            "a": {"grade": "A", "direction": "buy", "assumptions": {"revenue_growth_pct": 8}},
            "b": {"grade": "BB", "direction": "sell", "assumptions": {"revenue_growth_pct": 3}}})
        self.assertEqual(s["primary_conflict_driver"], "assumption")
        self.assertIn("agent A assumes 8% revenue growth, agent B assumes 3% revenue growth", s["audit_recommendation"])

    def test_the_residual_is_weighting_and_is_named_as_such(self):
        s = synthesize([_row("revenue", "MATCH")], None, {"a": {"grade": "A", "direction": "hold"},
                                                           "b": {"grade": "BBB", "direction": "hold"}})
        self.assertEqual(s["primary_conflict_driver"], "weighting")
        self.assertIn("weighed the same evidence differently", s["audit_recommendation"])

    def test_consensus_only_when_grade_direction_agree_and_no_hard_failure(self):
        same = {"a": {"grade": "A", "direction": "hold"}, "b": {"grade": "A", "direction": "hold"}}
        self.assertEqual(synthesize([], None, same)["consensus_grade"], {"grade": "A", "direction": "hold"})
        blocked = synthesize([], CHECKS, same)
        self.assertIsNone(blocked["consensus_grade"])
        self.assertIn("hard accounting check failed", blocked["consensus_reason"])
        self.assertFalse(blocked["needs_decision"])
        one = synthesize([], None, {"a": {"grade": "A", "direction": "hold"}, "b": None})
        self.assertEqual((one["primary_conflict_driver"], one["needs_decision"]), ("insufficient", False))

    def test_evidence_is_counted_not_scored(self):
        s = synthesize([_row("revenue", "MATCH"), _row("market_cap", "UNCORROBORATED", 3e12, None)], CHECKS,
                       {"a": None, "b": None})
        a, b = s["grade_candidates"]
        self.assertEqual(a["evidence"], {"match_filing": 1, "contradict_filing": 0, "unchecked": 0,
                                         "unbacked": 1, "hard_check_failures": 0})
        self.assertEqual((b["evidence"]["contradict_filing"], b["evidence"]["hard_check_failures"]), (1, 1))


def _gated_payload():
    syn = synthesize([_row("revenue", "MATCH")], None, {"a": {"grade": "A", "direction": "buy"},
                                                        "b": {"grade": "BB", "direction": "sell"}})
    return {"run_id": "r-g", "subject": "AAPL", "scope": "auditor", "gate_policy": G.GATE_POLICY, "halted": False,
            "session": {"initiated_at": "2026-09-26T00:00:00+00:00", "reasoning_objects": []},
            "reasoning_objects": [],
            "cross_agent_comparison": {"status": "COMPARED", "metric_comparisons": [], "synthesis": syn,
                                       "agent_a_conclusion": "x", "agent_b_conclusion": "y",
                                       "agent_a_numbers": [], "agent_b_numbers": [], "divergent_numbers": []}}


GOOD = {"decided_by": "Divij", "rationale": "Margins and cash back the stronger view.", "cited_items": ["grade"]}


class TestGradeGate(unittest.TestCase):

    def test_differing_grades_open_a_grade_item(self):
        g = G.gate_state(_gated_payload(), [])
        self.assertEqual((g["status"], g["pending"]), (G.AWAITING_DECISION, ["grade"]))
        item = g["items"][0]
        self.assertEqual((item["kind"], item["candidates"]["a"]["grade"], item["driver"]), ("grade", "A", "weighting"))

    def test_decisions_for_a_grade(self):
        p = _gated_payload()
        with self.assertRaises(G.DecisionError):
            G.validate_decision(p, {**GOOD, "decision": "set_grade", "final_grade": "A+"})
        with self.assertRaises(G.DecisionError):
            G.validate_decision(p, {**GOOD, "decision": "both_wrong"})
        self.assertEqual(G.validate_decision(p, {**GOOD, "decision": "accept_b"})["final_grade"], "BB")
        chosen = G.validate_decision(p, {**GOOD, "decision": "set_grade", "final_grade": "BBB"})
        g = G.gate_state(p, [{**chosen, "decision_id": "d1", "decided_at": "t"}])
        self.assertEqual(g["status"], G.DECIDED)
        self.assertEqual((g["decided_grade"]["grade"], g["decided_grade"]["decided_by"]), ("BBB", "Divij"))

    def test_investors_see_the_kind_of_disagreement_and_only_a_human_grade(self):
        p = _gated_payload()
        pending = G.redact_for_scope(p, "investor", G.gate_state(p, []))
        syn = pending["cross_agent_comparison"]["synthesis"]
        self.assertEqual((syn["primary_conflict_driver"], syn["withheld"]), ("weighting", True))
        self.assertNotIn("grade_candidates", syn)
        self.assertNotIn("candidates", pending["gate"]["items"][0])
        self.assertNotIn('"A"', json.dumps(pending["cross_agent_comparison"]))
        decided = G.gate_state(p, [{**G.validate_decision(p, {**GOOD, "decision": "set_grade", "final_grade": "BBB"}),
                                    "decision_id": "d1", "decided_at": "t"}])
        out = G.redact_for_scope(p, "investor", decided)
        self.assertEqual(out["gate"]["decided_grade"]["grade"], "BBB")


class _Db(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.path = Path(self._tmpdir) / "t.db"
        self._db = patch("web.db.DB_PATH", self.path)
        self._db.start()
        from web import db
        self.db = db

    def tearDown(self):
        self._db.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestStore(_Db):

    def test_a_b3_era_table_is_rebuilt_with_final_grade(self):
        conn = sqlite3.connect(self.path)
        conn.executescript(
            "CREATE TABLE runs (run_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, scope TEXT NOT NULL, status TEXT NOT NULL,"
            " initiated_at TEXT NOT NULL, completed_at TEXT, confidence REAL, payload_json TEXT NOT NULL,"
            " created_at TEXT NOT NULL DEFAULT '2026-09-26T00:00:00Z');"
            "CREATE TABLE gate_decisions (decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, decided_by TEXT NOT NULL,"
            " decision TEXT NOT NULL CHECK(decision IN ('accept_a','accept_b','both_wrong','not_a_conflict',"
            "'override_value','confirmed_error')), final_value REAL, rationale TEXT NOT NULL, cited_items TEXT NOT NULL,"
            " decided_at TEXT NOT NULL);")
        conn.execute("INSERT INTO runs (run_id, ticker, scope, status, initiated_at, payload_json)"
                     " VALUES ('r1','X','auditor','COMPLETE','2026-09-26','{}')")
        conn.execute("INSERT INTO gate_decisions VALUES ('d1','r1','Divij','confirmed_error',NULL,"
                     "'The check is right, A misquoted.','[\"x\"]','2026-09-26T10:00:00Z')")
        conn.commit()
        conn.close()
        self.db.init_db()
        got = self.db.get_decisions("r1")
        self.assertEqual((got[0]["decision_id"], got[0]["final_grade"]), ("d1", None))
        self.db.insert_decision("r1", {**GOOD, "decision": "set_grade", "final_grade": "BBB", "final_value": None})
        self.assertEqual(self.db.get_decisions("r1")[1]["final_grade"], "BBB")


class TestRoute(_Db):
    """A ticker compare whose agents agree on every figure but not on the grade."""

    def setUp(self):
        super().setUp()
        self.db.init_db()
        facts = json.loads((Path(__file__).parent / "fixtures" / "edgar_aapl_companyfacts_sample.json").read_text(encoding="utf-8"))
        grades = iter([{"grade": "A", "direction": "buy"}, {"grade": "BB", "direction": "sell"}])
        self._p = [
            patch("web.server._build_adapter", side_effect=lambda cfg: _agent(TWO)),
            patch("web.server._build_model_call", side_effect=lambda cfg: reply(next(grades))),
            patch("web.server.lookup_cik", return_value="0000320193"),
            patch("web.server.fetch_company_facts", return_value=facts),
            patch("validation.cross_validation._max_concurrency", return_value=1),  # A then B: grades in order
        ]
        for p in self._p:
            p.start()
        from web.server import app
        self.client = TestClient(app)

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()
        super().tearDown()

    def auth(self, scope):
        return {"Authorization": "Bearer " + self.client.post("/api/auth/token", json={"scope": scope}).json()["access_token"]}

    def test_extract_classify_gate_decide(self):
        d = self.client.post("/api/compare", json={"ticker": "AAPL"}, headers=self.auth("auditor")).json()
        syn = d["cross_agent_comparison"]["synthesis"]
        self.assertEqual(syn["primary_conflict_driver"], "weighting")
        self.assertEqual([c["grade"] for c in syn["grade_candidates"]], ["A", "BB"])
        self.assertEqual(d["gate"]["pending"], ["grade"])
        self.assertTrue(any(s.get("kind") == "extract" for s in d["steps"]))
        run_id = d["run_id"]
        before = self.client.get(f"/api/runs/{run_id}", headers=self.auth("investor")).json()
        self.assertIsNone(before["gate"]["decided_grade"])
        r = self.client.post(f"/api/runs/{run_id}/decisions", headers=self.auth("auditor"),
                             json={**GOOD, "decision": "set_grade", "final_grade": "BBB"})
        self.assertEqual(r.status_code, 200, r.text)
        after = self.client.get(f"/api/runs/{run_id}", headers=self.auth("investor")).json()
        self.assertEqual(after["gate"]["decided_grade"]["grade"], "BBB")
        self.assertNotIn("grade_candidates", after["cross_agent_comparison"]["synthesis"])


if __name__ == "__main__":
    unittest.main()
