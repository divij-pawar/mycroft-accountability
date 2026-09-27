"""
The decision gate must withhold disputed figures from investors everywhere they
appear — including the run trace's search results (found 2026-09-26: the investor
read of gated run ec1a3b44 carried agent A's disputed "1998" inside a Nintendo
search result in step 5's detail; logs/RUN_LOG.md correction entry of that date).

Covered here: stored reads (/api/runs/{id}), the live stream (/api/compare/stream),
/api/runs/{id}/source-snippet and GET /api/runs/{id}/decisions, at investor scope
while AWAITING_DECISION — and that auditors, and investors once decided, still get
the full trace. No network, no live model.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.directive import DirectiveVersion
from core.parsing import AgentResponse, _parse_response
from validation import gate as G

_FIXTURE = Path(__file__).resolve().parent.parent / "web" / "frontend" / "tests" / "fixtures" / "run_compare_gated_auditor.json"
DISPUTED = ("1998", "1996")


class TestStripSearchContent(unittest.TestCase):

    def test_tool_step_keeps_query_and_urls_only(self):
        step = {"seq": 5, "phase": "agent_a", "kind": "tool", "query": "first Pokemon NA release",
                "detail": "query: 'first Pokemon NA release' · {'results': [{'content': '| 1998 | Nintendo…'}]}",
                "urls": ["https://press.nintendo.com/CompanyHistory"],
                "results": [{"url": "https://press.nintendo.com/CompanyHistory", "title": "1998", "snippet": "1998"}]}
        out = G.strip_search_content(step)
        self.assertEqual(out["detail"], "query: 'first Pokemon NA release'")
        self.assertNotIn("results", out)
        self.assertEqual(out["urls"], step["urls"])
        self.assertTrue(out["result_withheld"])
        self.assertIn("results", step, "must not mutate the stored step")

    def test_agent_error_keeps_only_its_type(self):
        out = G.strip_search_content({"phase": "agent_b", "kind": "llm",
                                      "error": "StructuralParseError: conclusion echoes 'released in 1996'"})
        self.assertEqual(out["error"], "StructuralParseError")


class _Db(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db = patch("web.db.DB_PATH", Path(self._tmpdir) / "t.db")
        self._db.start()
        from web import db
        db.init_db()
        self.db = db

    def tearDown(self):
        self._db.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def auth(self, scope):
        tok = self.client.post("/api/auth/token", json={"scope": scope}).json()["access_token"]
        return {"Authorization": f"Bearer {tok}"}


class TestStoredGatedRun(_Db):
    """The real gated run (live, 2026-09-25), stored as-is, read back through the routes."""

    def setUp(self):
        super().setUp()
        self.run = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        self.run.pop("gate", None)  # the fixture is a read; store the record, not the view
        self.db.insert_run(self.run)
        from web.server import app
        self.client = TestClient(app)
        self.path = f"/api/runs/{self.run['run_id']}"

    def test_the_fixture_really_carries_the_disputed_values_in_its_trace(self):
        steps = json.dumps(self.run["steps"], ensure_ascii=False)
        self.assertIn("1998", steps)  # otherwise the next test proves nothing

    def test_investor_read_contains_neither_disputed_value(self):
        body = json.dumps(self.client.get(self.path, headers=self.auth("investor")).json(), ensure_ascii=False)
        for value in DISPUTED:
            self.assertNotIn(value, body)

    def test_auditors_still_get_the_full_trace(self):
        body = json.dumps(self.client.get(self.path, headers=self.auth("auditor")).json(), ensure_ascii=False)
        self.assertIn("1998", body)

    def test_once_decided_investors_get_the_trace_back(self):
        self.db.insert_decision(self.run["run_id"], {
            "decision": "accept_a", "decided_by": "Test reviewer", "cited_items": ["release_year"],
            "final_value": None, "rationale": "Nintendo's own history gives 1998 for North America."})
        run = self.client.get(self.path, headers=self.auth("investor")).json()
        self.assertEqual(run["gate"]["status"], G.DECIDED)
        self.assertIn("1998", json.dumps(run["steps"], ensure_ascii=False))

    def test_snippet_route_is_withheld_for_investors_while_pending(self):
        url = "https://press.nintendo.com/CompanyHistory"
        got = self.client.get(f"{self.path}/source-snippet", params={"url": url}, headers=self.auth("investor")).json()
        self.assertEqual(got["status"], "withheld")
        auditor = self.client.get(f"{self.path}/source-snippet", params={"url": url}, headers=self.auth("auditor")).json()
        self.assertNotEqual(auditor["status"], "withheld")


class TestDecisionsRoute(_Db):

    def test_a_pending_checks_arithmetic_is_withheld_from_investors(self):
        run = {
            "run_id": "r-check", "subject": "AAPL", "scope": "investor", "gate_policy": G.GATE_POLICY,
            "session": {"initiated_at": "2026-09-25T00:00:00+00:00"},
            "cross_agent_comparison": {"status": "COMPARED", "metric_comparisons": [], "structural_flags": {"checks": [
                {"key": "source:agent_a:revenue", "rule": "matches_source", "kind": "hard", "scope": "agent_a",
                 "who": "Agent A", "outcome": "fail", "plain": "Revenue matches", "gates": True,
                 "math": "cited $94.9 billion; given $109.4B", "metrics": ["revenue"]}]}},
        }
        self.db.insert_run(run)
        from web.server import app
        self.client = TestClient(app)
        investor = self.client.get("/api/runs/r-check/decisions", headers=self.auth("investor")).json()
        self.assertIsNone(investor["items"][0]["math"])
        self.assertNotIn("94.9", json.dumps(investor))
        auditor = self.client.get("/api/runs/r-check/decisions", headers=self.auth("auditor")).json()
        self.assertIn("94.9", auditor["items"][0]["math"])


def _searching_agent(year: str):
    """A scripted agent that makes one search (through the route's own tool-event hook) and answers."""
    def build(cfg):
        def adapter(subject: str, context: str, directive: DirectiveVersion) -> AgentResponse:
            evt = {"seq": 1, "tool": "tavily_search", "url": None, "query": "first Pokemon game North America",
                   "args": {}, "phase": "finished", "status": "ok", "duration_ms": 5.0,
                   "result_preview": f"{{'results': [{{'content': 'released in {year}'}}]}}",
                   "urls": ["https://example.com/pokemon"],
                   "results": [{"url": "https://example.com/pokemon", "title": "Pokemon", "snippet": f"released in {year}"}]}
            cfg["_on_tool_event"](evt)
            return _parse_response("<thought_log>\n  Searched and read the result.\n</thought_log>\n"
                                   f"<conclusion>\n  The first Pokemon game reached North America in {year}.\n</conclusion>")
        return adapter
    return build


class TestLiveStream(_Db):

    def setUp(self):
        super().setUp()
        builders = iter([_searching_agent("1998"), _searching_agent("1996"),
                         _searching_agent("1998"), _searching_agent("1996")])
        self._p = patch("web.server._build_adapter", side_effect=lambda cfg: next(builders)(cfg))
        self._p.start()
        from web.server import app
        self.client = TestClient(app)

    def tearDown(self):
        self._p.stop()
        super().tearDown()

    def _stream(self, scope):
        with self.client.stream("POST", "/api/compare/stream", json={"subject": "First Pokemon game in North America"},
                                headers=self.auth(scope)) as r:
            return "".join(r.iter_text())

    def test_investor_stream_carries_no_search_text_and_the_result_is_gated(self):
        body = self._stream("investor")
        steps = [line for line in body.splitlines() if line.startswith("data:") and '"kind": "tool"' in line]
        self.assertTrue(steps, "the scripted agents did search")
        for line in steps:
            for value in DISPUTED:
                self.assertNotIn(value, line)
        result = json.loads([l for l in body.split("\n\n") if l.startswith("event: result")][0].split("data: ", 1)[1])
        self.assertEqual(result["gate"]["status"], G.AWAITING_DECISION)
        for value in DISPUTED:
            self.assertNotIn(value, json.dumps(result, ensure_ascii=False))
        # The auditor stream of the same kind of run keeps its search results.
        self.assertIn("released in 1998", self._stream("auditor"))


if __name__ == "__main__":
    unittest.main()
