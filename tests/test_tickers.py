"""
Filing a chat run under the company its question names (datasources/tickers.py, 2026-10-06).

Run 03bd3ca5, "What was Apples revenue last quarter?", was filed as ticker WHAT, the
question's first word. The snapshot it reads is in the repo, so these tests need no network.
"""

from __future__ import annotations

from tests.support import ADMIN_HEADERS
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from datasources.tickers import SNAPSHOT, _name_key, ticker_for


class TestTickerFor(unittest.TestCase):

    def test_run_03bd3ca5_is_apple(self):
        self.assertEqual(ticker_for("What was Apples revenue last quarter?"), "AAPL")

    def test_names_as_people_write_them(self):
        cases = {
            "What was Microsoft's revenue last quarter?": "MSFT",
            "What did Bank of America earn?": "BAC",
            "What was the CEO pay at Ford?": "F",          # FORD MOTOR CO, by its first word
            "American Airlines revenue": "AAL",            # the longer name wins over "American"
            "Johnson & Johnson sales": "JNJ",
            "Is the Home Depot growing?": "HD",
            "Tell me about Alphabet": "GOOGL",             # the larger listing of one company
        }
        for question, ticker in cases.items():
            self.assertEqual(ticker_for(question), ticker, question)

    def test_tickers_written_as_tickers(self):
        self.assertEqual(ticker_for("How is $TSLA doing"), "TSLA")
        self.assertEqual(ticker_for("Compare NVDA and AMD margins"), "NVDA")  # first one named

    def test_questions_about_no_company(self):
        for question in ("When was Inception released?", "Mad Max release year", "What is EPS?",
                         "What is the GDP of France?", "Who won the World Series?",
                         "Did the revenue match expectations?", "", None):
            self.assertIsNone(ticker_for(question), question)

    def test_a_common_word_is_a_company_only_when_capitalised(self):
        self.assertEqual(ticker_for("Target quarterly sales"), "TGT")
        self.assertIsNone(ticker_for("did they hit the target this quarter?"))

    def test_name_keys_drop_legal_forms(self):
        self.assertEqual(_name_key("Apple Inc."), "apple")
        self.assertEqual(_name_key("AMAZON COM INC"), "amazon")
        self.assertEqual(_name_key("The Home Depot, Inc."), "home depot")

    def test_snapshot_says_where_it_came_from(self):
        first = SNAPSHOT.read_text(encoding="utf-8").splitlines()[0]
        self.assertTrue(first.startswith("# Source: https://www.sec.gov/files/company_tickers.json, fetched "))


class TestChatRunsAreFiledByCompany(unittest.TestCase):

    def setUp(self):
        from tests.support import make_scripted_adapter, no_model_extraction

        self._tmpdir = tempfile.mkdtemp()
        self._patchers = [
            patch("web.db.DB_PATH", Path(self._tmpdir) / "test.db"),
            patch("web.server._build_adapter", side_effect=lambda cfg: make_scripted_adapter("none")),
            no_model_extraction(),
        ]
        for p in self._patchers:
            p.start()
        from fastapi.testclient import TestClient
        from web.db import init_db
        from web.server import app
        init_db()
        self.client = TestClient(app)
        token = self.client.post("/api/auth/token", json={"scope": "auditor"}, headers=ADMIN_HEADERS).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _chat(self, message):
        return self.client.post("/api/chat", json={"message": message, "context": ""},
                                headers=self.headers).json()

    def test_payload_session_and_row_agree(self):
        run = self._chat("What was Apples revenue last quarter?")
        self.assertEqual(run["ticker"], "AAPL")
        self.assertEqual(run["session"]["ticker"], "AAPL")
        listed = self.client.get("/api/runs", params={"ticker": "AAPL"}, headers=self.headers).json()
        self.assertIn(run["run_id"], [r["run_id"] for r in listed])

    def test_no_company_is_chat(self):
        run = self._chat("When was Inception released?")
        self.assertEqual((run["ticker"], run["session"]["ticker"]), ("CHAT", "CHAT"))


class TestStoredTicker(unittest.TestCase):

    def test_payload_ticker_wins_then_subject(self):
        from web.db import _payload_ticker
        self.assertEqual(_payload_ticker({"ticker": "msft", "subject": "What about Apple?"}), "MSFT")
        self.assertEqual(_payload_ticker({"ticker": None, "subject": "What about Apple?"}), "AAPL")
        self.assertEqual(_payload_ticker({"subject": "When was Inception released?"}), "CHAT")


if __name__ == "__main__":
    unittest.main()
