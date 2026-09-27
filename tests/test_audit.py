"""
B6 — the audit record (validation/audit.py) and its routes, GET /api/runs/{id}/audit
and GET /api/runs/{id}/export.md.

Built on a real stored run (live, 2026-09-26, AAPL `8ecb0922`: every figure matches the
filing, the extracted grades differ, the gate awaits a grade decision), stored into a
temporary database and read back through the routes at each scope.

Pinned: the payload has the design's shape and reads every field from the record; an
investor export carries no agent grade and no disputed value, exactly like an
investor read; the Markdown is a readable review with a decision history; the
export never changes the stored record.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from validation.audit import AUDIT_FORMAT, audit_record, to_markdown

_FIXTURES = Path(__file__).resolve().parent.parent / "web" / "frontend" / "tests" / "fixtures"
RUN = json.loads((_FIXTURES / "run_compare_b5_auditor.json").read_text(encoding="utf-8"))

KEYS = {"metric_comparisons", "structural_flags", "primary_conflict_driver", "grade_candidates", "consensus_grade",
        "consensus_reason", "audit_recommendation", "gate_status", "decisions", "decided_grade", "filings"}


class TestRecord(unittest.TestCase):

    def test_the_designs_shape_read_from_the_record(self):
        a = audit_record(RUN, scope="auditor")
        self.assertTrue(KEYS <= set(a))
        self.assertEqual(a["format"], AUDIT_FORMAT)
        self.assertEqual(a["primary_conflict_driver"], RUN["cross_agent_comparison"]["synthesis"]["primary_conflict_driver"])
        self.assertEqual(a["gate_status"], "AWAITING_DECISION")
        self.assertEqual([c["grade"] for c in a["grade_candidates"]], ["AAA", "A"])
        self.assertEqual(a["filings"][0]["accn"], "0000320193-26-000020")
        self.assertTrue(a["filings"][0]["url"].endswith("0000320193-26-000020-index.htm"))

    def test_markdown_is_a_review_not_a_dump(self):
        md = to_markdown(audit_record(RUN, scope="auditor"))
        for section in ("# Review: AAPL", "## Summary", "## Figures, agent by agent", "## Accounting checks",
                        "## Grade candidates (model judgments)", "## Sources"):
            self.assertIn(section, md)
        self.assertIn("| Net income | $29.79 billion | $29.8 billion | 0.03% | Match |", md)
        self.assertIn("Why the views differ: a stated assumption differs.", md)
        self.assertIn("whether the run is adequate is a human judgment", md)
        self.assertNotIn("{", md.split("## Sources")[0])  # no raw JSON in the report

    def test_decisions_and_a_human_grade_are_reported_with_who_and_why(self):
        run = json.loads(json.dumps(RUN))
        d = {"decision_id": "d1", "decided_by": "Divij", "decision": "set_grade", "final_grade": "AA", "final_value": None,
             "rationale": "Margins, not growth, are the open question.", "cited_items": ["grade"],
             "decided_at": "2026-09-26T12:00:00Z", "superseded": False}
        run["gate"] = {**run["gate"], "status": "DECIDED", "pending": [], "decisions": [d],
                       "decided_grade": {"grade": "AA", "decided_by": "Divij", "decided_at": "2026-09-26T12:00:00Z", "decision_id": "d1"}}
        md = to_markdown(audit_record(run, scope="auditor"))
        self.assertIn("**Grade: AA**, set by Divij", md)
        self.assertIn("set_grade → AA on grade", md)
        self.assertIn("> Margins, not growth, are the open question.", md)

    def test_table_cells_stay_one_line_and_escape_the_separator(self):
        run = json.loads(json.dumps(RUN))
        run["cross_agent_comparison"]["metric_comparisons"][0]["label"] = "A | B\nsplit"
        self.assertIn("| A \\| B split |", to_markdown(audit_record(run, scope="auditor")))


class TestRoutes(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db = patch("web.db.DB_PATH", Path(self._tmpdir) / "t.db")
        self._db.start()
        from web import db
        db.init_db()
        stored = json.loads(json.dumps(RUN))
        stored.pop("gate", None)  # the fixture is a read; store the record
        db.insert_run(stored)
        db.insert_run({"run_id": "chat-1", "subject": "hi", "scope": "auditor", "conclusion": "x",
                       "session": {"initiated_at": "2026-09-26T00:00:00+00:00"}})
        self.db = db
        from web.server import app
        self.client = TestClient(app)
        self.rid = RUN["run_id"]

    def tearDown(self):
        self._db.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def auth(self, scope):
        return {"Authorization": "Bearer " + self.client.post("/api/auth/token", json={"scope": scope}).json()["access_token"]}

    def test_markdown_is_the_default_export_and_downloads_as_a_file(self):
        r = self.client.get(f"/api/runs/{self.rid}/export.md", headers=self.auth("auditor"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/markdown"))
        self.assertIn(f'filename="review-{self.rid[:8]}-auditor.md"', r.headers["content-disposition"])
        self.assertIn("## Grade candidates (model judgments)", r.text)

    def test_an_investor_export_carries_what_an_investor_read_carries(self):
        md = self.client.get(f"/api/runs/{self.rid}/export.md", headers=self.auth("investor")).text
        self.assertNotIn("AAA", md)
        self.assertIn("Pending human review", md)
        self.assertIn("Grade: none published", md)
        record = self.client.get(f"/api/runs/{self.rid}/audit", headers=self.auth("investor")).json()
        self.assertTrue(record["grades_withheld"])
        self.assertIsNone(record["grade_candidates"])
        self.assertNotIn('"AAA"', json.dumps(record))

    def test_exporting_changes_nothing_stored(self):
        before = json.dumps(self.db.get_run(self.rid), sort_keys=True)
        self.client.get(f"/api/runs/{self.rid}/export.md", headers=self.auth("auditor"))
        self.client.get(f"/api/runs/{self.rid}/audit", headers=self.auth("investor"))
        self.assertEqual(json.dumps(self.db.get_run(self.rid), sort_keys=True), before)

    def test_only_compare_runs_and_only_existing_ones(self):
        self.assertEqual(self.client.get("/api/runs/chat-1/audit").status_code, 422)
        self.assertEqual(self.client.get("/api/runs/nope/export.md").status_code, 404)


if __name__ == "__main__":
    unittest.main()
