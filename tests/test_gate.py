"""
BG — the human decision gate (validation/gate.py, web/db.py gate_decisions, the
/api/runs/{id}/decisions routes, and scope-aware reads).

What is pinned here:
  - the handoff condition (P4): a run is AWAITING_DECISION while any MISMATCH row has
    no decision, DECIDED once every one has; nothing else opens it;
  - decisions are validated, refused rather than repaired (P3), append-only (P7), and
    a later one supersedes an earlier one without erasing it;
  - investor-scope reads of a gated run withhold the contested figures and the
    conclusions that state them, announced by a marker, and get them back once decided;
  - runs stored before the gate are never gated retroactively.

No network, no live model: agents are scripted per slot, the same injection boundary
tests/test_compare_route.py uses.
"""

from __future__ import annotations

import copy
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.directive import DirectiveVersion
from core.parsing import AgentResponse, _parse_response
from validation import gate as G


def _row(metric, status, a=None, b=None, label=None):
    return {
        "metric": metric, "label": label or metric, "family": "currency", "status": status,
        "value_a": a, "value_b": b, "raw_a": None if a is None else f"${a}", "raw_b": None if b is None else f"${b}",
        "period_a": None, "period_b": None, "variance_pct": 4.3 if status == "MISMATCH" else None,
        "note": None, "flags": status in ("MISMATCH", "UNCORROBORATED"),
    }


def _payload(rows, *, policy=G.GATE_POLICY, status="COMPARED"):
    ro = {
        "reasoning_id": "r1", "agent_id": "financial", "conclusion": "Revenue was $420M.",
        "reasoning_steps": ["x"], "citations": [], "thought_log": "secret reasoning $420M",
        "raw_output": {"text": "raw"}, "llm_tokens": 5, "directive_text": "d", "context_window": {},
    }
    p = {
        "run_id": "run-1", "scope": "auditor", "halted": False,
        "reasoning_objects": [ro],
        "session": {"run_id": "run-1", "initiated_at": "2026-09-25T00:00:00+00:00", "directive_text": "d",
                    "reasoning_objects": [copy.deepcopy(ro)]},
        "cross_agent_comparison": {
            "status": status, "agent_a_conclusion": "Revenue was $420M.", "agent_b_conclusion": "Revenue was $380M.",
            "agent_a_numbers": ["420M"], "agent_b_numbers": ["380M"], "divergent_numbers": ["420M", "380M"],
            "metric_comparisons": rows,
        },
        "claims": {"a": [{"text": "$420M"}], "b": []},
    }
    if policy is not None:
        p["gate_policy"] = policy
    return p


def _decision(did, cited, decision="accept_a"):
    return {"decision_id": did, "decided_by": "Divij", "decision": decision, "final_value": None,
            "rationale": "Checked against the 10-Q table.", "cited_items": cited, "decided_at": did}


MISMATCH_ROWS = [_row("revenue", "MISMATCH", 420e6, 380e6, "Revenue"), _row("net_income", "MATCH", 9e6, 9e6)]


class TestGateState(unittest.TestCase):

    def test_legacy_run_is_never_gated_retroactively(self):
        g = G.gate_state(_payload(MISMATCH_ROWS, policy=None), [])
        self.assertEqual(g["status"], G.NOT_GATED)

    def test_a_mismatch_opens_the_gate(self):
        g = G.gate_state(_payload(MISMATCH_ROWS), [])
        self.assertEqual(g["status"], G.AWAITING_DECISION)
        self.assertEqual(g["pending"], ["revenue"])
        self.assertIn("not authenticated", g["identity_note"])

    def test_uncorroborated_and_matches_do_not_gate(self):
        rows = [_row("market_cap", "UNCORROBORATED", 3e12), _row("revenue", "MATCH", 1, 1)]
        self.assertEqual(G.gate_state(_payload(rows), [])["status"], G.NO_DECISION_NEEDED)

    def test_a_run_that_was_not_compared_needs_no_decision(self):
        self.assertEqual(G.gate_state(_payload(MISMATCH_ROWS, status="AGENT_A_HALTED"), [])["status"],
                         G.NO_DECISION_NEEDED)

    def test_cleared_only_when_every_mismatch_is_decided(self):
        rows = MISMATCH_ROWS + [_row("eps", "MISMATCH", 1.5, 1.6)]
        partial = G.gate_state(_payload(rows), [_decision("d1", ["revenue"])])
        self.assertEqual(partial["status"], G.AWAITING_DECISION)
        self.assertEqual(partial["pending"], ["eps"])
        done = G.gate_state(_payload(rows), [_decision("d1", ["revenue"]), _decision("d2", ["eps"])])
        self.assertEqual(done["status"], G.DECIDED)

    def test_later_decision_supersedes_but_keeps_history(self):
        g = G.gate_state(_payload(MISMATCH_ROWS), [_decision("d1", ["revenue"]),
                                                   _decision("d2", ["revenue"], "accept_b")])
        self.assertEqual(g["items"][0]["decision_id"], "d2")
        self.assertEqual([d["decision_id"] for d in g["decisions"]], ["d2", "d1"])  # newest first
        self.assertEqual([d["superseded"] for d in g["decisions"]], [False, True])


class TestValidateDecision(unittest.TestCase):
    GOOD = {"decision": "accept_a", "decided_by": "Divij", "rationale": "Matches the filed 10-Q table row.",
            "cited_items": ["revenue"], "final_value": None}

    def _refused(self, payload=None, **changes):
        with self.assertRaises(G.DecisionError):
            G.validate_decision(payload or _payload(MISMATCH_ROWS), {**self.GOOD, **changes})

    def test_accepts_a_complete_decision_and_trims(self):
        out = G.validate_decision(_payload(MISMATCH_ROWS), {**self.GOOD, "decided_by": "  Divij  ",
                                                            "cited_items": ["revenue", "revenue"]})
        self.assertEqual(out["decided_by"], "Divij")
        self.assertEqual(out["cited_items"], ["revenue"])

    def test_refusals(self):
        self._refused(decision="looks_good")
        self._refused(rationale="fine")                        # too short to be a reason
        self._refused(decided_by=" ")
        self._refused(cited_items=[])
        self._refused(cited_items=["net_income"])              # a MATCH row is not decidable
        self._refused(decision="override_value")               # no value
        self._refused(final_value=400e6)                       # a value on a non-override decision
        self._refused(payload=_payload(MISMATCH_ROWS, policy=None))
        self._refused(payload=_payload([_row("revenue", "MATCH", 1, 1)]))

    def test_override_needs_one_figure_and_a_number(self):
        rows = MISMATCH_ROWS + [_row("eps", "MISMATCH", 1.5, 1.6)]
        self._refused(payload=_payload(rows), decision="override_value", final_value=1.0, cited_items=["revenue", "eps"])
        self._refused(decision="override_value", final_value=True)
        out = G.validate_decision(_payload(MISMATCH_ROWS), {**self.GOOD, "decision": "override_value", "final_value": 401e6})
        self.assertEqual(out["final_value"], 401e6)


class TestRedactForScope(unittest.TestCase):

    def test_auditor_sees_everything_plus_the_gate(self):
        p = _payload(MISMATCH_ROWS)
        g = G.gate_state(p, [])
        out = G.redact_for_scope(p, "auditor", g)
        self.assertEqual(out["gate"]["status"], G.AWAITING_DECISION)
        self.assertEqual(out["cross_agent_comparison"]["agent_a_conclusion"], "Revenue was $420M.")
        self.assertIn("thought_log", out["reasoning_objects"][0])
        self.assertNotIn("gate", p, "must not mutate the stored payload")

    def test_investor_pending_withholds_contested_material_only(self):
        p = _payload(MISMATCH_ROWS)
        out = G.redact_for_scope(p, "investor", G.gate_state(p, []))
        cmp = out["cross_agent_comparison"]
        self.assertIsNone(cmp["agent_a_conclusion"])
        self.assertEqual(cmp["divergent_numbers"], [])
        revenue, net = cmp["metric_comparisons"]
        self.assertTrue(revenue["withheld"])
        self.assertIsNone(revenue["value_a"])
        self.assertIsNone(revenue["raw_b"])
        self.assertEqual(net["value_a"], 9e6, "an agreed figure is not contested")
        self.assertNotIn("withheld", net)
        for ro in out["reasoning_objects"] + out["session"]["reasoning_objects"]:
            for key in ("conclusion", "thought_log", "raw_output", "context_window", "directive_text"):
                self.assertNotIn(key, ro)
        self.assertEqual(out["claims"], {"a": [], "b": []})
        self.assertIn("Pending human review", out["withheld_pending_review"])

    def test_investor_gets_figures_back_once_decided(self):
        p = _payload(MISMATCH_ROWS)
        out = G.redact_for_scope(p, "investor", G.gate_state(p, [_decision("d1", ["revenue"])]))
        self.assertEqual(out["cross_agent_comparison"]["agent_b_conclusion"], "Revenue was $380M.")
        self.assertEqual(out["cross_agent_comparison"]["metric_comparisons"][0]["value_b"], 380e6)
        self.assertNotIn("withheld_pending_review", out)
        self.assertEqual(out["reasoning_objects"][0]["conclusion"], "Revenue was $420M.")
        self.assertNotIn("thought_log", out["reasoning_objects"][0], "SEC-01 still applies")


class _TempDb(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_patcher = patch("web.db.DB_PATH", Path(self._tmpdir) / "test.db")
        self._db_patcher.start()
        from web import db
        self.db = db
        db.init_db()

    def tearDown(self):
        self._db_patcher.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestDecisionStore(_TempDb):

    def _stored_run(self):
        self.db.insert_run(_payload(MISMATCH_ROWS))
        fields = G.validate_decision(_payload(MISMATCH_ROWS), TestValidateDecision.GOOD)
        return self.db.insert_decision("run-1", fields)

    def test_round_trip_in_insertion_order(self):
        first = self._stored_run()
        second = self.db.insert_decision("run-1", {**TestValidateDecision.GOOD, "decision": "accept_b"})
        got = self.db.get_decisions("run-1")
        self.assertEqual([d["decision_id"] for d in got], [first["decision_id"], second["decision_id"]])
        self.assertEqual(got[0]["cited_items"], ["revenue"])
        self.assertEqual(self.db.get_decisions_for(["run-1", "none"])["none"], [])

    def test_append_only(self):
        self._stored_run()
        with self.db._connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE gate_decisions SET decision = 'accept_b'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM gate_decisions")

    def test_database_refuses_an_empty_rationale_even_past_the_route(self):
        self.db.insert_run(_payload(MISMATCH_ROWS))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.insert_decision("run-1", {**TestValidateDecision.GOOD, "rationale": "ok"})

    def test_ttl_purge_removes_decisions_with_their_run_and_restores_the_guard(self):
        self._stored_run()
        self.assertEqual(self.db.purge_old_runs(retention_days=-1), 1)
        self.assertEqual(self.db.get_decisions("run-1"), [])
        self.db.insert_run({**_payload(MISMATCH_ROWS), "run_id": "run-2"})
        self.db.insert_decision("run-2", {**TestValidateDecision.GOOD})
        with self.db._connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM gate_decisions")


# ── Through the HTTP routes ─────────────────────────────────────────────────────

def _agent(conclusion: str):
    def adapter(subject: str, context: str, directive: DirectiveVersion) -> AgentResponse:
        return _parse_response(
            "<thought_log>\n  Read the subject and answered from it.\n</thought_log>\n"
            f"<conclusion>\n  {conclusion}\n</conclusion>"
        )
    return adapter


class TestGateRoutes(_TempDb):

    def setUp(self):
        super().setUp()
        agents = iter([_agent("Inception was released in 2010."), _agent("Inception was released in 2014.")])
        self._adapter_patcher = patch("web.server._build_adapter", side_effect=lambda cfg: next(agents))
        self._adapter_patcher.start()
        from web.server import app
        self.client = TestClient(app)

    def tearDown(self):
        self._adapter_patcher.stop()
        super().tearDown()

    def _auth(self, scope):
        tok = self.client.post("/api/auth/token", json={"scope": scope}).json()["access_token"]
        return {"Authorization": f"Bearer {tok}"}

    def _compare(self, scope="auditor"):
        resp = self.client.post("/api/compare", json={"subject": "Inception release year"}, headers=self._auth(scope))
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def _decide(self, run_id, scope="auditor", **changes):
        body = {"decision": "accept_a", "decided_by": "Divij", "cited_items": ["release_year"],
                "rationale": "Box Office Mojo and the studio both give July 2010.", **changes}
        return self.client.post(f"/api/runs/{run_id}/decisions", json=body, headers=self._auth(scope))

    def test_a_disagreement_opens_the_gate_and_is_stored_as_gated(self):
        data = self._compare()
        self.assertEqual(data["gate"]["status"], G.AWAITING_DECISION)
        self.assertEqual(data["gate"]["pending"], ["release_year"])
        self.assertEqual(self.db.get_run(data["run_id"])["gate_policy"], G.GATE_POLICY)

    def test_investor_reads_are_withheld_until_a_human_decides(self):
        run_id = self._compare()["run_id"]
        investor = self.client.get(f"/api/runs/{run_id}", headers=self._auth("investor")).json()
        self.assertIsNone(investor["cross_agent_comparison"]["agent_b_conclusion"])
        self.assertTrue(investor["cross_agent_comparison"]["metric_comparisons"][0]["withheld"])
        self.assertTrue(all("conclusion" not in ro for ro in investor["reasoning_objects"]))
        session = self.client.get(f"/api/sessions/{run_id}", headers=self._auth("investor")).json()
        self.assertTrue(all("conclusion" not in ro for ro in session["reasoning_objects"]))
        auditor = self.client.get(f"/api/runs/{run_id}", headers=self._auth("auditor")).json()
        self.assertIn("2014", auditor["cross_agent_comparison"]["agent_b_conclusion"])

        self.assertEqual(self._decide(run_id, scope="investor").status_code, 403)
        self.assertEqual(self._decide(run_id, rationale="yes").status_code, 422)
        self.assertEqual(self._decide(run_id, cited_items=["revenue"]).status_code, 422)
        resp = self._decide(run_id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], G.DECIDED)

        after = self.client.get(f"/api/runs/{run_id}", headers=self._auth("investor")).json()
        self.assertIn("2014", after["cross_agent_comparison"]["agent_b_conclusion"])
        self.assertEqual(after["gate"]["decisions"][0]["decided_by"], "Divij")
        listed = self.client.get("/api/runs", headers=self._auth("investor")).json()
        self.assertEqual(listed[0]["gate"]["status"], G.DECIDED)

    def test_an_investor_starting_the_run_gets_it_withheld_live(self):
        data = self._compare(scope="investor")
        self.assertEqual(data["gate"]["status"], G.AWAITING_DECISION)
        self.assertIsNone(data["cross_agent_comparison"]["agent_a_conclusion"])
        self.assertIn("Pending human review", data["withheld_pending_review"])

    def test_no_token_reads_at_the_stored_scope(self):
        run_id = self._compare(scope="investor")["run_id"]
        self.assertIsNone(self.client.get(f"/api/runs/{run_id}").json()["cross_agent_comparison"]["agent_a_conclusion"])

    def test_pre_gate_runs_are_not_gated_and_take_no_decision(self):
        self.db.insert_run(_payload(MISMATCH_ROWS, policy=None))
        got = self.client.get("/api/runs/run-1", headers=self._auth("investor")).json()
        self.assertEqual(got["gate"]["status"], G.NOT_GATED)
        self.assertEqual(self._decide("run-1", cited_items=["revenue"]).status_code, 422)
        self.assertEqual(self.client.get("/api/runs/run-1/decisions").json()["status"], G.NOT_GATED)


if __name__ == "__main__":
    unittest.main()
