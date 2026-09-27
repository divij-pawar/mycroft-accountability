"""
Route-level tests for POST /api/compare.

Why this file exists
    web/self_report.py's no-route-tests entry: no route in web/server.py had a
    single automated test before this, /api/compare included — every check on
    it was a manual browser run. This file doesn't close that whole gap (the
    JS, step_trace.py's timing, and every other route are still untested), but
    it does lock in today's fix: extract_claims/verify_claims are now called
    per-producer in this route (previously only /api/chat did this — see
    web/self_report.py's fabrication-not-caught entry), and the route now uses
    contradiction_rule="concept_aware". Both are asserted here through a real
    HTTP call, not just at the validation/cross_validation.py unit level.

No network, no live model
    web.server.lookup_cik / fetch_company_facts are patched with a fixed
    companyfacts payload — the same convention tests/test_cross_validation.py's
    end-to-end tests use, just applied at the route's import site instead of via
    fetch_fn injection, because the route calls both functions with no fetch_fn
    parameter of its own. LangChain (the one provider now) always makes a live
    model call — there is no scripted provider anymore — so
    web.server._build_adapter itself is patched to return tests.support's
    scripted double instead, the same dependency-injection boundary the route's
    own code already goes through (adapters.registry.build_adapter).
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.support import make_scripted_adapter, no_model_extraction

_FACTS = {
    "facts": {
        "us-gaap": {
            "Assets":         {"units": {"USD": [{"end": "2026-03-31", "val": 383266000000.0}]}},
            "Revenues":       {"units": {"USD": [{"end": "2026-03-31", "val": 265595000000.0}]}},
            "NetIncomeLoss":  {"units": {"USD": [{"end": "2026-03-31", "val": 101464000000.0}]}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [{"end": "2026-03-31", "val": 6.45}]}},
            "EarningsPerShareBasic":   {"units": {"USD/shares": [{"end": "2026-03-31", "val": 6.50}]}},
            "OperatingIncomeLoss":     {"units": {"USD": [{"end": "2026-03-31", "val": 120000000000}]}},
        }
    }
}


class TestCompareRoute(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_patcher = patch("web.db.DB_PATH", Path(self._tmpdir) / "test.db")
        self._db_patcher.start()
        self._cik_patcher = patch("web.server.lookup_cik", return_value="0000320193")
        self._cik_patcher.start()
        self._facts_patcher = patch("web.server.fetch_company_facts", return_value=_FACTS)
        self._facts_patcher.start()
        self._adapter_patcher = patch(
            "web.server._build_adapter",
            side_effect=lambda cfg: make_scripted_adapter("none"),
        )
        self._adapter_patcher.start()
        self._extract_patcher = no_model_extraction()
        self._extract_patcher.start()

        # Import after patching so the route module's already-bound names are
        # the ones patch() replaced (web.server imports these by name at
        # module load, matching how the route itself calls them).
        from web.server import app
        self.client = TestClient(app)

    def tearDown(self):
        self._extract_patcher.stop()
        self._adapter_patcher.stop()
        self._facts_patcher.stop()
        self._cik_patcher.stop()
        self._db_patcher.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _token(self, scope: str) -> str:
        resp = self.client.post("/api/auth/token", json={"scope": scope})
        return resp.json()["access_token"]

    def _compare(self, scope: str = "auditor"):
        headers = {"Authorization": f"Bearer {self._token(scope)}"}
        return self.client.post(
            "/api/compare",
            json={"ticker": "AAPL"},
            headers=headers,
        )

    def test_returns_200_and_uses_concept_aware_rule(self):
        resp = self._compare()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["halted"])
        self.assertIsNone(data["error"])
        self.assertEqual(data["cross_agent_comparison"]["status"], "COMPARED")

    def test_claims_and_verification_rate_present_per_producer_at_auditor_scope(self):
        # This is the fabrication-not-caught fix: previously this route never
        # called extract_claims/verify_claims at all, on either producer.
        data = self._compare(scope="auditor").json()
        self.assertIn("a", data["claims"])
        self.assertIn("b", data["claims"])
        self.assertIsInstance(data["verification_rate"]["a"], float)
        self.assertIsInstance(data["verification_rate"]["b"], float)
        # tests.support's scripted thought_log echoes the context it was given, which
        # contains the real (patched) Assets/Revenues/NetIncomeLoss figures —
        # confirms claims are actually being extracted from real content, not
        # an empty stub.
        claim_texts = [c["text"] for c in data["claims"]["a"]]
        self.assertTrue(
            any("383266000000" in t for t in claim_texts),
            f"expected a claim citing the patched Assets figure, got {claim_texts}",
        )

    def test_stored_run_keeps_what_a_reviewer_needs_later(self):
        # Until 2026-09-24 a stored compare run kept 7 keys; reopened from history it
        # had no trace, no inputs and no per-figure rows.
        from web.db import get_run
        data = self._compare().json()
        stored = get_run(data["run_id"])
        for key in ("producers", "contexts", "facts", "steps", "claims"):
            self.assertIn(key, stored)
        self.assertTrue(any(s.get("kind") == "llm" for s in stored["steps"]))
        self.assertEqual(stored["facts"]["a"][0]["concept"], "Assets")
        cmp = stored["cross_agent_comparison"]
        self.assertEqual(cmp["contradiction_rule"], "concept_aware")
        self.assertIsInstance(cmp["metric_comparisons"], list)

    def test_generic_mode_uses_canonical_facts_with_years(self):
        headers = {"Authorization": f"Bearer {self._token('auditor')}"}
        data = self.client.post(
            "/api/compare", json={"subject": "Inception release year", "context": ""}, headers=headers,
        ).json()
        self.assertEqual(data["cross_agent_comparison"]["contradiction_rule"], "canonical_facts")

    def test_rule_can_be_opted_into_for_ticker_mode(self):
        headers = {"Authorization": f"Bearer {self._token('auditor')}"}
        data = self.client.post(
            "/api/compare", json={"ticker": "AAPL", "contradiction_rule": "canonical_facts"}, headers=headers,
        ).json()
        self.assertEqual(data["cross_agent_comparison"]["contradiction_rule"], "canonical_facts")

    def test_investor_scope_withholds_claims_but_keeps_the_rate(self):
        # Mirrors /api/chat's existing rule: verification_rate is a derived
        # number, not raw thought_log content, so it stays visible; the claims
        # themselves (which carry `context` — surrounding thought_log text)
        # are SEC-01 material and are withheld, same as thought_log itself.
        data = self._compare(scope="investor").json()
        self.assertEqual(data["claims"], {"a": [], "b": []})
        self.assertIsInstance(data["verification_rate"]["a"], float)
        self.assertIsInstance(data["verification_rate"]["b"], float)
        for ro in data["reasoning_objects"]:
            self.assertNotIn("thought_log", ro)


if __name__ == "__main__":
    unittest.main()
