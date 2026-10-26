"""
An agent session (Claude Code, Codex, Cursor...) as the agent, over MCP (web/mcp_server.py,
web/agent_sessions.py), and watching any run live (GET /api/runs/{id}/live, web/live.py).

What this locks in
    - start_compare hands the session the directive and its lens's filing context; the run
      shows up as in progress; the session's agent is recorded as "session: <label>".
    - The server, not the agent, checks the output contract: a reply without the two blocks
      comes back as a retry with the reason, a second bad reply halts that side, and a good one
      is accepted. The retry is the validation loop's own second attempt.
    - After the answer, the session is asked for the assessment JSON (the extraction call,
      asked of the same session); the run is then compared, checked, stored and gated as usual.
    - partner="session": a second session joins as agent B, and a figure the two disagree on
      opens the gate.
    - A session that never answers halts the run with a session-timeout error, and is released.
    - The layer's search tool: without a key it says to use your own search; with one, every
      query is recorded in the trace.
    - The watch stream replays a run's events (run_started ... result); an investor watcher
      gets no search text and no conclusions.
    - The real MCP protocol over HTTP (Streamable HTTP, the official client): the tools are
      listed and a whole run completes.

No model and no network: EDGAR is patched, agent B (when it's the server's) is scripted, and
the session is this test.
"""

from __future__ import annotations

from tests.support import ADMIN_HEADERS
import asyncio
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import ADMIN_HEADERS, make_scripted_adapter, no_model_extraction
from tests.test_concurrency_and_stream import _FACTS, _parse_sse

try:
    import mcp.server.fastmcp  # noqa: F401  (1.x; mcp 2.x renamed it, and server.py won't mount /mcp)
    HAS_MCP = True
except ImportError:
    HAS_MCP = False
needs_mcp = unittest.skipUnless(HAS_MCP, "the optional `mcp` 1.x package isn't installed (pip install -r requirements.txt)")

GOOD = ("<thought_log>Step 1: read the figures I was given.</thought_log>\n"
        "<conclusion>Net income was $101.464 billion and diluted EPS was $6.45.</conclusion>")
ASSESSMENT = json.dumps({"grade": "A", "direction": "hold", "assumptions": {}, "key_metrics": [], "key_points": []})


def call(name: str, **args) -> dict:
    from web.server import MCP
    return json.loads(asyncio.run(MCP.call_tool(name, args))[0].text)


class SessionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._patchers = [
            patch("web.db.DB_PATH", Path(self._tmp) / "test.db"),
            patch("web.server.lookup_cik", return_value="0000320193"),
            patch("web.server.fetch_company_facts", return_value=_FACTS),
            patch("web.server._build_adapter", side_effect=lambda cfg: make_scripted_adapter("none")),
            no_model_extraction(),
        ]
        for p in self._patchers:
            p.start()
        from web.db import init_db
        init_db()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def finish_a(self, start: dict) -> dict:
        """Answer, then the assessment, as a well-behaved session would."""
        r = call("submit", run_id=start["run_id"], slot="a", text=GOOD)
        if r["status"] == "assessment_requested":
            r = call("submit", run_id=start["run_id"], slot="a", text=ASSESSMENT)
        return r


@needs_mcp
class TestStartAndSubmit(SessionTestCase):
    def test_the_session_gets_its_lens_and_the_run_completes(self):
        start = call("start_compare", ticker="AAPL", agent_label="test-agent")
        self.assertEqual(start["status"], "your_turn")
        self.assertEqual(start["attempt"], 1)
        self.assertIn("<conclusion>", start["instructions"])           # the directive itself
        self.assertIn("NetIncomeLoss", start["context"])                # the financial lens
        self.assertTrue(start["watch"].endswith(f"#/live/{start['run_id']}"))

        r = self.finish_a(start)
        self.assertEqual(r["status"], "accepted")
        self.assertTrue(r["outcome"]["stored"])
        self.assertEqual(r["outcome"]["comparison_status"], "COMPARED")

        from web.db import get_run
        record = get_run(start["run_id"])
        self.assertEqual(record["producers"]["a"]["model"], "session: test-agent")
        self.assertTrue(record["producers"]["a"]["session"])
        self.assertFalse(record["producers"]["b"]["session"])
        steps = [s["label"] for s in record["steps"] if s.get("phase") == "agent_a"]
        self.assertIn("LLM attempt 1", steps)

    def test_the_server_checks_the_format_retries_once_then_accepts(self):
        start = call("start_compare", ticker="AAPL", agent_label="test-agent")
        retry = call("submit", run_id=start["run_id"], slot="a", text="Net income was $101 billion.")
        self.assertEqual(retry["status"], "your_turn")
        self.assertEqual(retry["attempt"], 2)
        self.assertIn("thought_log", retry["previous_attempt_failed"])
        self.assertEqual(self.finish_a(retry)["status"], "accepted")
        from web.db import get_run
        attempts = [ro["parse_status"] for ro in get_run(start["run_id"])["reasoning_objects"]
                    if ro["agent_id"] == "financial"]
        self.assertEqual(attempts, ["PARSE_FAILURE", "SUCCESS"])

    def test_two_bad_replies_halt_that_side(self):
        start = call("start_compare", ticker="AAPL", agent_label="test-agent")
        call("submit", run_id=start["run_id"], slot="a", text="no blocks")
        r = call("submit", run_id=start["run_id"], slot="a", text="still no blocks")
        self.assertEqual(r["status"], "halted")

    def test_bad_arguments_are_refused(self):
        self.assertIn("error", call("start_compare"))
        self.assertIn("error", call("start_compare", ticker="AAPL", subject="x"))
        self.assertIn("error", call("start_compare", ticker="AAPL", partner="gpt"))
        self.assertIn("error", call("submit", run_id="nope", slot="a", text=GOOD))


@needs_mcp
class TestSecondSession(SessionTestCase):
    def test_two_sessions_disagreeing_open_the_gate(self):
        q = "In what year was the first Pokemon game released in North America?"
        start = call("start_compare", subject=q, partner="session", agent_label="session-a")
        self.assertIn("join_compare", start["for_the_second_session"])
        joined = call("join_compare", run_id=start["run_id"], agent_label="session-b")
        self.assertEqual(joined["status"], "your_turn")
        self.assertIn("error", call("join_compare", run_id=start["run_id"]))  # only once

        answer = "<thought_log>Step 1: recalled it.</thought_log>\n<conclusion>It was released in {y}.</conclusion>"
        done = {}
        t = threading.Thread(target=lambda: done.setdefault(
            "b", call("submit", run_id=start["run_id"], slot="b", text=answer.format(y=1996))))
        t.start()
        a = call("submit", run_id=start["run_id"], slot="a", text=answer.format(y=1998))
        t.join(30)
        self.assertEqual(a["status"], "accepted")
        self.assertEqual(done["b"]["status"], "accepted")
        self.assertEqual(a["outcome"]["gate"], "AWAITING_DECISION")
        self.assertIn({"metric": "release_year", "status": "MISMATCH", "a": "1998", "b": "1996"},
                      a["outcome"]["figures"])


@needs_mcp
class TestTimeoutAndSearch(SessionTestCase):
    def test_a_silent_session_halts_the_run_and_is_released(self):
        with patch("web.agent_sessions.SESSION_TIMEOUT_S", 0.5):
            start = call("start_compare", ticker="AAPL", agent_label="slow-agent")
            from web import agent_sessions
            agent_sessions.get(start["run_id"]).done.wait(20)
        r = call("get_run", run_id=start["run_id"])
        self.assertFalse(r["stored"])
        self.assertIn("Agent session timed out", r["error"])
        self.assertIn("slow-agent", r["error"])

    def test_search_without_a_key_says_to_use_your_own(self):
        start = call("start_compare", ticker="AAPL")
        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}):
            r = call("search", run_id=start["run_id"], slot="a", query="apple revenue")
        self.assertIn("own web search", r["error"])
        self.finish_a(start)

    def test_search_with_a_key_is_recorded_in_the_trace(self):
        result = {"results": [{"title": "Apple 10-Q", "url": "https://example.com/aapl", "content": "Net income $101.5B"}]}
        start = call("start_compare", ticker="AAPL")
        # The layer's search is replaced, so no request leaves the machine.
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test"}),                 patch("web.server._layer_search", return_value=result) as layer_search:
            r = call("search", run_id=start["run_id"], slot="a", query="apple net income")
        layer_search.assert_called_once_with("apple net income")
        self.assertEqual(r["results"][0]["url"], "https://example.com/aapl")
        self.finish_a(start)
        from web.db import get_run
        tool_steps = [s for s in get_run(start["run_id"])["steps"] if s.get("kind") == "tool"]
        self.assertTrue(any(s.get("query") == "apple net income" for s in tool_steps), tool_steps)


@needs_mcp
class TestWatch(SessionTestCase):
    def _client(self):
        from fastapi.testclient import TestClient
        from web.server import app
        return TestClient(app)

    def test_a_run_is_listed_while_in_progress_and_its_events_replay(self):
        start = call("start_compare", ticker="AAPL", agent_label="test-agent")
        client = self._client()
        live = client.get("/api/runs/live").json()
        self.assertIn(start["run_id"], [r["run_id"] for r in live])
        self.finish_a(start)
        with client.stream("GET", f"/api/runs/{start['run_id']}/live") as resp:
            events = _parse_sse("".join(resp.iter_text()))
        names = [e for e, _ in events]
        self.assertEqual(names[0], "run_started")
        self.assertEqual(names[-1], "result")
        self.assertEqual(events[0][1]["started_by"], "session: test-agent")
        self.assertIn("agent_finished", names)
        self.assertNotIn(start["run_id"], [r["run_id"] for r in client.get("/api/runs/live").json()])

    def test_an_investor_watcher_gets_no_conclusions(self):
        start = call("start_compare", ticker="AAPL")
        self.finish_a(start)
        client = self._client()
        token = client.post("/api/auth/token", json={"scope": "investor"}, headers=ADMIN_HEADERS).json()["access_token"]
        with client.stream("GET", f"/api/runs/{start['run_id']}/live",
                           headers={"Authorization": f"Bearer {token}"}) as resp:
            events = _parse_sse("".join(resp.iter_text()))
        finished = [d for e, d in events if e == "agent_finished"]
        self.assertTrue(finished)
        self.assertTrue(all(d["conclusion"] is None for d in finished))

    def test_an_unknown_run_is_404(self):
        self.assertEqual(self._client().get("/api/runs/nope/live").status_code, 404)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@needs_mcp
class TestOverRealMcp(SessionTestCase):
    """The official MCP client over Streamable HTTP, against the app served by uvicorn."""

    def test_tools_are_listed_and_a_run_completes(self):
        import uvicorn
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        from web.server import app

        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        self.addCleanup(lambda: (setattr(server, "should_exit", True), thread.join(10)))

        async def session_run():
            async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp", headers={"Authorization": "Bearer " + ADMIN_HEADERS["X-Admin-Key"]}) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = sorted(t.name for t in (await session.list_tools()).tools)
                    start = json.loads((await session.call_tool("start_compare", {"ticker": "AAPL"})).content[0].text)
                    r = json.loads((await session.call_tool("submit", {"run_id": start["run_id"], "slot": "a",
                                                                       "text": GOOD})).content[0].text)
                    if r["status"] == "assessment_requested":
                        r = json.loads((await session.call_tool("submit", {"run_id": start["run_id"], "slot": "a",
                                                                           "text": ASSESSMENT})).content[0].text)
                    return names, start, r

        names, start, r = asyncio.run(session_run())
        self.assertEqual(names, ["get_run", "join_compare", "search", "start_compare", "submit"])
        self.assertEqual(r["status"], "accepted")
        self.assertTrue(r["outcome"]["stored"])
        from web.db import get_run
        # The client reported its own name; it is recorded as given.
        self.assertTrue(get_run(start["run_id"])["producers"]["a"]["model"].startswith("session: "))


if __name__ == "__main__":
    unittest.main()
