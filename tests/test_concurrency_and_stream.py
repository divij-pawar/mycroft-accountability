"""
Concurrent agents, thread-safe step traces, and the live event-stream routes.

What this locks in
    - StepTrace survives many threads appending at once (unique, gap-free seq) and
      publishes step_started/step_finished to subscribers.
    - run_cross_agent_validation's two agents genuinely overlap by default. Proved
      with a threading.Barrier both adapters must reach together, so a sequential
      run can't pass by accident.
    - CROSS_AGENT_MAX_CONCURRENCY=1 restores strict A-then-B.
    - reasoning_objects stay ordered A-then-B even when B finishes first.
    - /api/compare/stream and /api/chat/stream emit run_started, step events and
      agent_finished, then a `result` identical in shape to the plain route's.

No network, no live model: fixture/scripted adapters only, EDGAR patched at the
route's import site (same convention as tests/test_compare_route.py).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters.fixture_adapter import make_fixture_adapter
from adapters.langchain_adapter import _call_tool, _parse_across_turns, _result_urls
from core.schemas import AgentID
from tests.support import make_scripted_adapter, no_model_extraction
from validation.cross_validation import ComparisonStatus, run_cross_agent_validation
from web.step_trace import StepTrace


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        events.append((event, data))
    return events


class TestStepTraceThreadSafety(unittest.TestCase):

    def test_concurrent_appends_get_unique_gap_free_seq(self):
        trace = StepTrace()
        threads = [
            threading.Thread(target=lambda: [trace.note("p", "n") for _ in range(200)])
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seqs = sorted(s["seq"] for s in trace.to_list())
        self.assertEqual(seqs, list(range(1, 1601)))
        self.assertTrue(all(s["started_at"] for s in trace.to_list()))

    def test_subscriber_sees_start_and_finish_as_copies(self):
        trace = StepTrace()
        seen = []
        trace.subscribe(lambda event, step: seen.append((event, step)))
        with trace.record("p", "timed", extra={"kind": "fetch"}) as step:
            step["detail"] = "found during the step"
        trace.note("p", "instant", extra={"kind": "compare"})

        self.assertEqual([e for e, _ in seen], ["step_started", "step_finished", "step_finished"])
        started, finished = seen[0][1], seen[1][1]
        self.assertIsNone(started["duration_ms"])            # snapshot taken before it finished
        self.assertIsNotNone(finished["duration_ms"])
        self.assertEqual(finished["detail"], "found during the step")
        self.assertEqual(finished["kind"], "fetch")
        started["label"] = "mutated by listener"
        self.assertEqual(trace.to_list()[0]["label"], "timed")  # listener got a copy

    def test_broken_listener_does_not_break_the_run(self):
        trace = StepTrace()

        def broken(event, step):
            raise RuntimeError("browser went away")

        trace.subscribe(broken)
        with trace.record("p", "still recorded"):
            pass
        self.assertEqual(len(trace.to_list()), 1)
        self.assertEqual(trace.to_list()[0]["status"], "ok")


def _barrier_adapter(barrier: threading.Barrier, conclusion: str):
    inner = make_fixture_adapter(conclusion)

    def adapter(subject, context, directive):
        barrier.wait()  # raises BrokenBarrierError if the other agent never arrives
        return inner(subject, context, directive)

    return adapter


class TestConcurrentAgents(unittest.TestCase):

    def _run(self, adapter_a, adapter_b, **kwargs):
        return run_cross_agent_validation(
            "subject", "ctx", "ctx", AgentID.GENERIC_A, AgentID.GENERIC_B,
            adapter_a, adapter_b, **kwargs,
        )

    def test_agents_overlap_by_default(self):
        barrier = threading.Barrier(2, timeout=5)
        result, _ = self._run(
            _barrier_adapter(barrier, "Revenue was $4.20 million."),
            _barrier_adapter(barrier, "Revenue was $4.20 million."),
        )
        self.assertEqual(result.status, ComparisonStatus.COMPARED)
        self.assertFalse(result.contradiction_flag)

    def test_max_concurrency_one_runs_a_then_b(self):
        order = []

        def recording(name, conclusion):
            inner = make_fixture_adapter(conclusion)

            def adapter(subject, context, directive):
                order.append(f"{name}-start")
                time.sleep(0.05)
                order.append(f"{name}-end")
                return inner(subject, context, directive)

            return adapter

        with patch.dict(os.environ, {"CROSS_AGENT_MAX_CONCURRENCY": "1"}):
            self._run(recording("a", "x $1.00."), recording("b", "x $1.00."))
        self.assertEqual(order, ["a-start", "a-end", "b-start", "b-end"])

    def test_objects_stay_a_then_b_when_b_finishes_first(self):
        slow_inner = make_fixture_adapter("Revenue was $4.20 million.")

        def slow_a(subject, context, directive):
            time.sleep(0.2)
            return slow_inner(subject, context, directive)

        finished = []
        _, objects = self._run(
            slow_a,
            make_fixture_adapter("Revenue was $4.20 million."),
            on_agent_finished=lambda slot, conclusion, halted: finished.append(slot),
        )
        self.assertEqual(finished, ["b", "a"])  # B really did finish first...
        self.assertEqual(                        # ...and the record is still A-then-B
            [ro.agent_id for ro in objects], [AgentID.GENERIC_A, AgentID.GENERIC_B]
        )

    def test_concurrent_and_sequential_results_match(self):
        a, b = "Revenue was $4.20 million.", "Revenue was $5.10 million."
        conc, _ = self._run(make_fixture_adapter(a), make_fixture_adapter(b))
        seq, _ = self._run(make_fixture_adapter(a), make_fixture_adapter(b), concurrent=False)
        for field in ("status", "contradiction_flag", "divergent_numbers", "score", "agreement"):
            self.assertEqual(getattr(conc, field), getattr(seq, field), field)


class _FakeTool:
    """Scripted stand-in for TavilySearch: returns or raises each response in turn."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TestCallTool(unittest.TestCase):
    """A tool that *returns* an error must be recorded as an error (found live 2026-09-24)."""

    OK = {"results": [{"url": "https://ok.example"}]}

    def _call(self, tool, args=None):
        import asyncio
        return asyncio.run(_call_tool(tool, args or {"query": "q", "topic": "bogus"}, "q"))

    def test_first_call_succeeds(self):
        tool = _FakeTool(self.OK)
        self.assertEqual(self._call(tool), (self.OK, "ok", False))
        self.assertEqual(len(tool.calls), 1)

    def test_returned_error_then_query_only_retry_succeeds(self):
        tool = _FakeTool({"error": Exception("Error 400: Bad Request")}, self.OK)
        result, status, retried = self._call(tool)
        self.assertEqual((result, status, retried), (self.OK, "ok", True))
        self.assertEqual(tool.calls[1], {"query": "q"})

    def test_returned_error_twice_is_an_error_not_a_result(self):
        tool = _FakeTool({"error": Exception("Error 400: Bad Request")},
                         {"error": Exception("Error 400: Bad Request")})
        result, status, retried = self._call(tool)
        self.assertEqual((status, retried), ("error", True))
        self.assertIn("Error 400", result)

    def test_raised_error_then_retry_succeeds(self):
        tool = _FakeTool(ValueError("invalid time_range"), self.OK)
        self.assertEqual(self._call(tool), (self.OK, "ok", True))


class TestParseAcrossTurns(unittest.TestCase):
    """A response split across the tool loop is one response (found live 2026-09-24)."""

    def test_final_message_alone_still_wins_when_it_parses(self):
        whole = "<thought_log>\nr\n</thought_log>\n<conclusion>\nc\n</conclusion>"
        self.assertEqual(_parse_across_turns(["ignored preamble", whole]).conclusion, "c")

    def test_opening_written_before_the_tool_call_is_not_lost(self):
        # The live shape: thought_log opened in the tool-calling turn, closed after the results.
        turns = ["<thought_log>\nI will search for the release year.",
                 "Results confirm 2010.\n</thought_log>\n<conclusion>\nInception was released in 2010.\n</conclusion>"]
        response = _parse_across_turns(turns)
        self.assertIn("I will search", response.thought_log)
        self.assertEqual(response.conclusion, "Inception was released in 2010.")

    def test_failure_records_everything_the_model_wrote(self):
        from core.parsing import StructuralParseError
        with self.assertRaises(StructuralParseError) as ctx:
            _parse_across_turns(["<thought_log>\nhalf", "no conclusion here"])
        self.assertEqual(ctx.exception.raw_response, "<thought_log>\nhalf\nno conclusion here")


class TestResultUrls(unittest.TestCase):

    def test_extracts_urls_in_rank_order(self):
        result = {"results": [{"url": "https://a.example"}, {"title": "no url"}, {"url": "https://b.example"}]}
        self.assertEqual(_result_urls(result), ["https://a.example", "https://b.example"])

    def test_error_string_has_no_urls(self):
        self.assertEqual(_result_urls("Tool error: boom"), [])


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


class TestStreamRoutes(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._patchers = [
            patch("web.db.DB_PATH", Path(self._tmpdir) / "test.db"),
            patch("web.server.lookup_cik", return_value="0000320193"),
            patch("web.server.fetch_company_facts", return_value=_FACTS),
            patch("web.server._build_adapter", side_effect=lambda cfg: make_scripted_adapter("none")),
            no_model_extraction(),
        ]
        for p in self._patchers:
            p.start()
        from fastapi.testclient import TestClient
        from web.db import init_db
        from web.server import app
        # /api/chat relies on the app's startup event to create tables; a bare
        # TestClient never fires startup, so create them against the patched path.
        init_db()
        self.client = TestClient(app)

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _headers(self, scope="auditor"):
        token = self.client.post("/api/auth/token", json={"scope": scope}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def _stream(self, path, body):
        with self.client.stream("POST", path, json=body, headers=self._headers()) as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
            return _parse_sse("".join(resp.iter_text()))

    def test_compare_stream_event_sequence(self):
        events = self._stream("/api/compare/stream", {"ticker": "AAPL"})
        names = [e for e, _ in events]

        self.assertEqual(names[0], "run_started")
        self.assertEqual(names[-1], "result")
        self.assertIn("producers", events[0][1])
        self.assertEqual(
            sorted(d["agent"] for e, d in events if e == "agent_finished"), ["a", "b"]
        )
        kinds = {d.get("kind") for e, d in events if e.startswith("step_")}
        self.assertTrue({"fetch", "llm", "compare"} <= kinds, kinds)
        # Every agent step finishes before the comparison note is published.
        compare_idx = next(i for i, (e, d) in enumerate(events) if d and d.get("kind") == "compare")
        last_agent_finished = max(i for i, (e, _) in enumerate(events) if e == "agent_finished")
        self.assertLess(last_agent_finished, compare_idx)

        result = events[-1][1]
        self.assertEqual(result["cross_agent_comparison"]["status"], "COMPARED")
        self.assertEqual(result["run_id"], events[0][1]["run_id"])

    def test_compare_stream_result_matches_plain_route_shape(self):
        streamed = self._stream("/api/compare/stream", {"ticker": "AAPL"})[-1][1]
        plain = self.client.post(
            "/api/compare", json={"ticker": "AAPL"}, headers=self._headers()
        ).json()
        self.assertEqual(set(streamed), set(plain))

    def test_chat_stream_ends_with_result(self):
        events = self._stream("/api/chat/stream", {"message": "What was revenue?", "context": ""})
        self.assertEqual(events[0][0], "run_started")
        self.assertEqual(events[-1][0], "result")
        self.assertFalse(events[-1][1]["halted"])
        self.assertTrue(any(d.get("kind") == "llm" for e, d in events if e.startswith("step_")))


if __name__ == "__main__":
    unittest.main()
