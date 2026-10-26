"""
Who may call what (Week 1, 2026-10-26; ledger: audit-criticals, session-scope-leak, no-route-tests).

Three layers of the same question:

  1. Issuing a token: an investor token is open, an auditor token needs the admin key,
     and a token can't be forged from the source code.
  2. The matrix: for every route on the app, the status an unauthenticated caller, an
     investor token and an auditor token each get. The test walks `app.routes`, so a route
     added without a row here fails the suite: every route needs an auth decision.
  3. What a read carries: the same run read with no token, an investor token and an auditor
     token, and that the disputed figures of a gated run reach only the auditor.

Network-free and model-free. Each test that mutates state runs on a temporary database.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import jwt
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.support import ADMIN_HEADERS, ADMIN_KEY, make_scripted_adapter

_FIXTURE = Path(__file__).resolve().parent.parent / "web" / "frontend" / "tests" / "fixtures" / "run_compare_gated_auditor.json"
DISPUTED = ("1998", "1996")


class _App(unittest.TestCase):
    """The app on a temporary database, with the one real gated run stored in it."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db = patch("web.db.DB_PATH", Path(self._tmpdir) / "t.db")
        self._db.start()
        from web import db
        db.init_db()
        self.db = db
        self.run = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        self.run.pop("gate", None)
        db.insert_run(self.run)
        from web.server import app
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self._db.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def token(self, scope):
        r = self.client.post("/api/auth/token", json={"scope": scope}, headers=ADMIN_HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["access_token"]

    def headers(self, who):
        return {} if who == "none" else {"Authorization": f"Bearer {self.token(who)}"}


class TestTokenIssue(_App):

    def test_an_investor_token_is_open(self):
        r = self.client.post("/api/auth/token", json={"scope": "investor"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["scope"], "investor")

    def test_an_auditor_token_needs_the_admin_key(self):
        r = self.client.post("/api/auth/token", json={"scope": "auditor"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("X-Admin-Key", r.json()["detail"])

    def test_a_wrong_admin_key_is_refused(self):
        r = self.client.post("/api/auth/token", json={"scope": "auditor"}, headers={"X-Admin-Key": ADMIN_KEY + "x"})
        self.assertEqual(r.status_code, 403)

    def test_the_admin_key_gets_an_auditor_token(self):
        r = self.client.post("/api/auth/token", json={"scope": "auditor"}, headers=ADMIN_HEADERS)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["scope"], "auditor")

    def test_with_no_admin_key_configured_auditor_tokens_are_refused_and_say_why(self):
        with patch.dict(os.environ):
            os.environ.pop("ACCOUNTABILITY_ADMIN_KEY", None)
            r = self.client.post("/api/auth/token", json={"scope": "auditor"}, headers=ADMIN_HEADERS)
        self.assertEqual(r.status_code, 403)
        self.assertIn("ACCOUNTABILITY_ADMIN_KEY", r.json()["detail"])

    def test_an_empty_admin_key_configured_does_not_match_an_empty_header(self):
        with patch.dict(os.environ, {"ACCOUNTABILITY_ADMIN_KEY": ""}):
            r = self.client.post("/api/auth/token", json={"scope": "auditor"}, headers={"X-Admin-Key": ""})
        self.assertEqual(r.status_code, 403)

    def test_insecure_dev_mode_restores_the_open_behaviour(self):
        with patch.dict(os.environ, {"ACCOUNTABILITY_INSECURE_DEV": "1"}):
            r = self.client.post("/api/auth/token", json={"scope": "auditor"})
        self.assertEqual(r.status_code, 200)


class TestTokenSecret(_App):

    def _forged(self, secret):
        now = datetime.now(timezone.utc)
        return jwt.encode({"sub": "x", "scope": "auditor", "iat": now, "exp": now + timedelta(hours=1)},
                          secret, algorithm="HS256")

    def test_a_token_signed_with_the_old_public_secret_is_rejected(self):
        from web.auth import _DEV_SECRET
        with patch.dict(os.environ):
            os.environ.pop("ACCOUNTABILITY_SECRET", None)
            os.environ.pop("ACCOUNTABILITY_INSECURE_DEV", None)
            r = self.client.get(f"/api/runs/{self.run['run_id']}/flags",
                                headers={"Authorization": f"Bearer {self._forged(_DEV_SECRET)}"})
        self.assertEqual(r.status_code, 401)

    def test_the_configured_secret_signs_and_verifies(self):
        with patch.dict(os.environ, {"ACCOUNTABILITY_SECRET": "a-test-secret-of-some-length-1234567890"}):
            token = self.client.post("/api/auth/token", json={"scope": "auditor"}, headers=ADMIN_HEADERS).json()["access_token"]
            self.assertEqual(jwt.decode(token, "a-test-secret-of-some-length-1234567890", algorithms=["HS256"])["scope"], "auditor")
            r = self.client.get(f"/api/runs/{self.run['run_id']}/flags", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 200)

    def test_without_a_configured_secret_the_process_secret_is_random_and_stable(self):
        from web import auth
        with patch.dict(os.environ):
            os.environ.pop("ACCOUNTABILITY_SECRET", None)
            os.environ.pop("ACCOUNTABILITY_INSECURE_DEV", None)
            first = auth._secret()
            self.assertEqual(first, auth._secret())
            self.assertNotEqual(first, auth._DEV_SECRET)
            self.assertGreaterEqual(len(first), 32)

    def test_an_expired_token_is_a_401_that_says_so(self):
        now = datetime.now(timezone.utc)
        from web.auth import _secret
        old = jwt.encode({"sub": "x", "scope": "auditor", "iat": now - timedelta(hours=9), "exp": now - timedelta(hours=1)},
                         _secret(), algorithm="HS256")
        r = self.client.get(f"/api/runs/{self.run['run_id']}/flags", headers={"Authorization": f"Bearer {old}"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("expired", r.json()["detail"])

    def test_a_token_with_an_unknown_scope_is_a_403(self):
        from web.auth import _secret
        now = datetime.now(timezone.utc)
        odd = jwt.encode({"sub": "x", "scope": "root", "iat": now, "exp": now + timedelta(hours=1)}, _secret(), algorithm="HS256")
        r = self.client.get(f"/api/runs/{self.run['run_id']}/flags", headers={"Authorization": f"Bearer {odd}"})
        self.assertEqual(r.status_code, 403)

    def test_garbage_is_a_401(self):
        r = self.client.get(f"/api/runs/{self.run['run_id']}/flags", headers={"Authorization": "Bearer not.a.token"})
        self.assertEqual(r.status_code, 401)


# ── The matrix ─────────────────────────────────────────────────────────────────
# (method, route path as declared) -> (a request path, request kwargs, status for none / investor / auditor).
# None for a status means "not exercised here" (the route runs a model; its auth is covered for
# the caller that must fail, and the allowed callers are covered by the route's own tests).
# "{rid}" is the stored gated run; "{nope}" is an id that doesn't exist.

RID = "{rid}"
NOPE = "nope-0000"

PUBLIC_READ = (200, 200, 200)
NEEDS_TOKEN_WRITE_MODEL = (401, None, None)
AUDITOR_ONLY_404 = (401, 403, 404)   # an unknown run: an allowed caller reaches the 404

MATRIX: dict[tuple[str, str], tuple[str, dict, tuple]] = {
    ("POST", "/api/auth/token"): ("/api/auth/token", {"json": {"scope": "investor"}}, (200, 200, 200)),
    ("GET", "/"): ("/", {"follow_redirects": False}, None),  # redirect or 503 depending on a frontend build
    ("GET", "/api/config"): ("/api/config", {}, PUBLIC_READ),
    ("POST", "/api/config"): ("/api/config", {"json": {}}, (401, 403, 200)),
    ("POST", "/api/chat"): ("/api/chat", {"json": {"message": "x"}}, NEEDS_TOKEN_WRITE_MODEL),
    ("POST", "/api/chat/stream"): ("/api/chat/stream", {"json": {"message": "x"}}, NEEDS_TOKEN_WRITE_MODEL),
    ("POST", "/api/compare"): ("/api/compare", {"json": {"ticker": "AAPL"}}, NEEDS_TOKEN_WRITE_MODEL),
    ("POST", "/api/compare/stream"): ("/api/compare/stream", {"json": {"ticker": "AAPL"}}, NEEDS_TOKEN_WRITE_MODEL),
    ("GET", "/api/runs/live"): ("/api/runs/live", {}, PUBLIC_READ),
    ("GET", "/api/runs/{run_id}/live"): (f"/api/runs/{NOPE}/live", {}, (404, 404, 404)),
    ("GET", "/api/runs/contradictions"): ("/api/runs/contradictions", {}, PUBLIC_READ),
    ("GET", "/api/runs"): ("/api/runs", {}, PUBLIC_READ),
    ("GET", "/api/runs/drift"): ("/api/runs/drift?ticker=AAPL", {}, PUBLIC_READ),
    ("GET", "/api/runs/{run_id}"): (f"/api/runs/{RID}", {}, PUBLIC_READ),
    ("GET", "/api/facts/excerpt"): ("/api/facts/excerpt?cik=x&accn=x&concept=x&end=x", {}, (422, 422, 422)),
    ("GET", "/api/runs/{run_id}/source-snippet"): (f"/api/runs/{RID}/source-snippet?url=https://example.com", {}, PUBLIC_READ),
    ("GET", "/api/runs/{run_id}/audit"): (f"/api/runs/{RID}/audit", {}, PUBLIC_READ),
    ("GET", "/api/runs/{run_id}/export.md"): (f"/api/runs/{RID}/export.md", {}, PUBLIC_READ),
    ("GET", "/api/runs/{run_id}/decisions"): (f"/api/runs/{RID}/decisions", {}, PUBLIC_READ),
    ("POST", "/api/runs/{run_id}/decisions"): (f"/api/runs/{NOPE}/decisions",
                                                {"json": {"decision": "accept_a", "decided_by": "T", "rationale": "x" * 30,
                                                          "cited_items": []}}, AUDITOR_ONLY_404),
    ("POST", "/api/runs/{run_id}/replay"): (f"/api/runs/{NOPE}/replay", {}, AUDITOR_ONLY_404),
    ("POST", "/api/runs/{run_id}/flags"): (f"/api/runs/{NOPE}/flags", {"json": {"flag_type": "Other"}}, AUDITOR_ONLY_404),
    ("GET", "/api/runs/{run_id}/flags"): (f"/api/runs/{NOPE}/flags", {}, AUDITOR_ONLY_404),
    ("GET", "/api/sessions"): ("/api/sessions", {}, PUBLIC_READ),
    ("GET", "/api/sessions/{session_id}"): (f"/api/sessions/{NOPE}", {}, (404, 404, 404)),
    ("DELETE", "/api/runs"): ("/api/runs", {}, (401, 403, 403)),   # an auditor still needs ACCOUNTABILITY_ALLOW_CLEAR=1
    ("POST", "/api/analysis/stream"): ("/api/analysis/stream", {"json": {"message": "Should I buy AAPL?"}}, NEEDS_TOKEN_WRITE_MODEL),
    ("GET", "/api/analysis"): ("/api/analysis", {}, (401, 200, 200)),
    ("GET", "/api/analysis/{run_id}"): (f"/api/analysis/{NOPE}", {}, (401, 404, 404)),
    ("GET", "/api/directive"): ("/api/directive", {}, PUBLIC_READ),
    ("GET", "/api/self-report"): ("/api/self-report", {}, PUBLIC_READ),
}


INTERNAL_TIER = ("thought_log", "raw_output", "llm_tokens", "directive_text")


def _internal_tier_found(node, path="") -> list[str]:
    """Every key of SEC-01's internal tier that holds a value anywhere in `node`."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in INTERNAL_TIER and v not in (None, "", [], {}):
                found.append(f"{path}/{k}")
            found += _internal_tier_found(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += _internal_tier_found(v, f"{path}[{i}]")
    return found


def _declared_routes(app) -> set[tuple[str, str]]:
    return {(m, r.path) for r in app.routes if isinstance(r, APIRoute)
            for m in r.methods if m not in ("HEAD", "OPTIONS")}


class TestEveryRouteHasAnAuthDecision(_App):

    def test_no_route_is_missing_from_the_matrix_and_none_is_stale(self):
        declared = _declared_routes(self.app)
        listed = set(MATRIX)
        self.assertEqual(
            declared - listed, set(),
            "A route was added with no row in MATRIX: decide who may call it, then add it.")
        self.assertEqual(listed - declared, set(), "MATRIX lists a route that no longer exists.")

    def test_each_caller_gets_the_status_the_matrix_says(self):
        problems = []
        for (method, route), (path, kwargs, expected) in sorted(MATRIX.items()):
            if expected is None:
                continue
            for who, want in zip(("none", "investor", "auditor"), expected):
                if want is None:
                    continue
                kw = {k: v for k, v in kwargs.items()}
                r = self.client.request(method, path.replace(RID, self.run["run_id"]), headers=self.headers(who), **kw)
                if r.status_code != want:
                    problems.append(f"{method} {route} as {who}: got {r.status_code}, want {want}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_the_mcp_endpoint_needs_the_admin_key(self):
        # Past the key check the MCP session manager isn't running here (no lifespan), which is a
        # 500; what matters is that only the right key gets past the 401.
        client = TestClient(self.app, raise_server_exceptions=False)
        self.assertEqual(client.post("/mcp", json={}).status_code, 401)
        investor = {"Authorization": f"Bearer {self.token('investor')}"}
        self.assertEqual(client.post("/mcp", json={}, headers=investor).status_code, 401)
        wrong = {"Authorization": f"Bearer {ADMIN_KEY}x"}
        self.assertEqual(client.post("/mcp", json={}, headers=wrong).status_code, 401)
        right = {"Authorization": f"Bearer {ADMIN_KEY}"}
        self.assertNotEqual(client.post("/mcp", json={}, headers=right).status_code, 401)


class TestServerKeySurvivesTestDiscovery(unittest.TestCase):
    """
    Found 2026-10-26 by running the server: web/self_report.py counts the suite by importing the test
    modules, tests/support.py set the admin key as it was imported, and the running server's own key was
    replaced by a string that sits in the repo. A fresh process, because support is already imported here.
    """

    def test_counting_the_tests_in_a_server_process_leaves_its_admin_key_alone(self):
        import subprocess
        import sys
        code = (
            "import os; os.environ['ACCOUNTABILITY_ADMIN_KEY']='the-servers-own-key'\n"
            "os.environ.pop('SOME_TEST_ONLY_VAR', None)\n"
            "from web.self_report import _discover_test_counts\n"
            "r = _discover_test_counts()\n"
            "assert r['error'] is None, r\n"
            "print(os.environ.get('ACCOUNTABILITY_ADMIN_KEY'))\n"
        )
        root = Path(__file__).resolve().parent.parent
        out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-1500:])
        self.assertEqual(out.stdout.strip().splitlines()[-1], "the-servers-own-key")

    def test_the_test_key_is_not_a_string_in_the_repo(self):
        self.assertNotIn(ADMIN_KEY, Path(__file__).read_text())
        self.assertGreaterEqual(len(ADMIN_KEY), 32)


class TestClearAll(_App):

    def test_an_auditor_cannot_clear_without_the_server_flag(self):
        r = self.client.delete("/api/runs", headers=self.headers("auditor"))
        self.assertEqual(r.status_code, 403)
        self.assertIn("ACCOUNTABILITY_ALLOW_CLEAR", r.json()["detail"])
        self.assertIsNotNone(self.db.get_run(self.run["run_id"]), "the run must still be there")

    def test_with_the_flag_an_auditor_can_and_an_investor_still_cannot(self):
        with patch.dict(os.environ, {"ACCOUNTABILITY_ALLOW_CLEAR": "1"}):
            self.assertEqual(self.client.delete("/api/runs", headers=self.headers("investor")).status_code, 403)
            self.assertEqual(self.client.delete("/api/runs").status_code, 401)
            r = self.client.delete("/api/runs", headers=self.headers("auditor"))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.db.get_run(self.run["run_id"]))


class TestWhatAReadCarries(_App):
    """A read with no token is served as an investor, never at the scope the run was stored under."""

    def _body(self, path, who):
        r = self.client.get(path, headers=self.headers(who))
        self.assertEqual(r.status_code, 200, r.text)
        return json.dumps(r.json(), ensure_ascii=False)

    def test_the_fixture_is_an_auditor_stored_run_that_really_carries_the_disputed_values(self):
        self.assertEqual(self.run["scope"], "auditor")
        self.assertIn("1998", json.dumps(self.run, ensure_ascii=False))

    def test_no_token_reads_as_an_investor_on_every_read_route(self):
        rid = self.run["run_id"]
        for path in (f"/api/runs/{rid}", "/api/runs", f"/api/runs/{rid}/audit", f"/api/runs/{rid}/decisions",
                     "/api/sessions", "/api/runs/contradictions"):
            body = self._body(path, "none")
            for value in DISPUTED:
                self.assertNotIn(value, body, f"{path} with no token carried {value}")
            self.assertNotIn("thought_log", body, f"{path} with no token carried a thought log")

    def test_no_token_export_carries_neither_value(self):
        r = self.client.get(f"/api/runs/{self.run['run_id']}/export.md")
        self.assertEqual(r.status_code, 200)
        for value in DISPUTED:
            self.assertNotIn(value, r.text)

    def test_no_token_snippet_is_withheld(self):
        r = self.client.get(f"/api/runs/{self.run['run_id']}/source-snippet",
                            params={"url": "https://press.nintendo.com/CompanyHistory"})
        self.assertEqual(r.json()["status"], "withheld")

    def test_the_investor_token_reads_the_same_as_no_token(self):
        rid = self.run["run_id"]
        self.assertEqual(self._body(f"/api/runs/{rid}", "none"), self._body(f"/api/runs/{rid}", "investor"))

    def test_the_auditor_still_reads_everything(self):
        body = self._body(f"/api/runs/{self.run['run_id']}", "auditor")
        self.assertIn("1998", body)
        self.assertIn("1996", body)

    def test_an_investor_cannot_read_flags_or_record_anything(self):
        rid = self.run["run_id"]
        investor = self.headers("investor")
        self.assertEqual(self.client.get(f"/api/runs/{rid}/flags", headers=investor).status_code, 403)
        self.assertEqual(self.client.post(f"/api/runs/{rid}/flags", json={"flag_type": "Other"}, headers=investor).status_code, 403)
        self.assertEqual(self.client.post(f"/api/runs/{rid}/replay", headers=investor).status_code, 403)
        self.assertEqual(self.client.post("/api/config", json={"seed": 1}, headers=investor).status_code, 403)

    def test_config_posted_without_a_token_changes_nothing(self):
        from web import server
        before = dict(server._config)
        self.assertEqual(self.client.post("/api/config", json={"seed": 99999}).status_code, 401)
        self.assertEqual(server._config, before)


class TestRoutesTheLedgerListedAsUntested(_App):
    """/api/chat, /api/config, /api/directive and /api/self-report had no route test (ledger: no-route-tests)."""

    def test_chat_with_a_token_runs_and_stores_a_run(self):
        with patch("web.server._build_adapter", side_effect=lambda cfg: make_scripted_adapter("none")):
            r = self.client.post("/api/chat", json={"message": "Is the sky blue?"}, headers=self.headers("auditor"))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("run_id", body)
        self.assertIsNotNone(self.db.get_run(body["run_id"]))

    def test_chat_as_an_investor_carries_no_internal_tier_anywhere_in_the_response(self):
        with patch("web.server._build_adapter", side_effect=lambda cfg: make_scripted_adapter("none")):
            r = self.client.post("/api/chat", json={"message": "Is the sky blue?"}, headers=self.headers("investor"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(_internal_tier_found(r.json()), [])

    def test_chat_as_an_auditor_does_carry_it(self):
        with patch("web.server._build_adapter", side_effect=lambda cfg: make_scripted_adapter("none")):
            r = self.client.post("/api/chat", json={"message": "Is the sky blue?"}, headers=self.headers("auditor"))
        self.assertNotEqual(_internal_tier_found(r.json()), [])

    def test_config_clamps_and_validates(self):
        from web import server
        saved = dict(server._config)
        try:
            ok = self.client.post("/api/config", json={"confidence_score": 5, "temperature": 0.123456},
                                  headers=self.headers("auditor"))
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["confidence_score"], 1.0)
            self.assertEqual(ok.json()["temperature"], 0.12)
            bad = self.client.post("/api/config", json={"agent_id": "not-an-agent"}, headers=self.headers("auditor"))
            self.assertEqual(bad.status_code, 400)
            self.assertEqual(server._config["agent_id"], saved["agent_id"])
        finally:
            server._config.clear()
            server._config.update(saved)

    def test_config_get_reports_the_provider_and_has_no_secrets(self):
        body = self.client.get("/api/config").json()
        self.assertEqual(body["provider"], "langchain")
        self.assertFalse([k for k in body if "key" in k.lower() or "token" in k.lower()])

    def test_directive_names_its_version(self):
        from core.directive import get_active_directive
        body = self.client.get("/api/directive").json()
        self.assertEqual(body["version"], get_active_directive().version)
        self.assertTrue(body["text"])

    def test_self_report_lists_issues_with_counts_that_add_up(self):
        body = self.client.get("/api/self-report").json()
        self.assertTrue(body["counts"]["issues_total"] >= body["counts"]["issues_open"])
        self.assertEqual(body["counts"]["issues_total"], len(body["known_issues"]))
        self.assertIn("deployment_status", body)

    def test_contradictions_and_drift_answer_on_an_empty_store(self):
        self.assertEqual(self.client.get("/api/runs/drift", params={"ticker": "ZZZZ"}).json(), {"ticker": "ZZZZ", "points": []})
        self.assertIsInstance(self.client.get("/api/runs/contradictions").json(), list)

    def test_replay_of_an_unknown_run_is_a_404_for_an_auditor(self):
        r = self.client.post(f"/api/runs/{NOPE}/replay", headers=self.headers("auditor"))
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
