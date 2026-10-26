"""
Plan-then-search, citations checked against what the agent retrieved, and confidence
lowered by what the checks found (2026-10-05, after run 03bd3ca5).

That run, the human's first chat run: llama3.2 made one search, answered correctly, and
cited every fact to https://www.macrumors.com/stocks/aapl/revenue, a URL none of its
five results had (it fused MacRumors' name with stockanalysis.com's path, and the page
is a 404). The verifier reported "couldn't check", the consistency probe's different
figure was flagged and nothing acted on it, and the run kept 0.75 STANDARD.

Network-free and model-free: the model, the search tool and every fetch are replaced.
"""

from __future__ import annotations

from tests.support import ADMIN_HEADERS
import asyncio
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from validation.claims import extract_claims_from_response
from validation.verification import (
    CHECK_CONFIRMED, CHECK_NOT_RETRIEVED, CHECK_PAGE_MISSING, CHECK_UNREACHABLE,
    normalize_url, urls_in_text, verify_claims,
)

HAS_LANGCHAIN = all(importlib.util.find_spec(m) is not None
                    for m in ("langchain_core", "langchain_tavily"))
needs_langchain = unittest.skipUnless(HAS_LANGCHAIN, "langchain-core / langchain-tavily not installed")

INVENTED = "https://www.macrumors.com/stocks/aapl/revenue"
REAL = "https://stockanalysis.com/stocks/aapl/revenue"

ANSWER_INVENTED = (
    "<thought_log>Apple's revenue for the quarter ending June 27, 2026 was $109.42B "
    f"[SOURCE: MacRumors, {INVENTED}].</thought_log>\n"
    f"<conclusion>Apple's revenue last quarter was $109.42B [SOURCE: MacRumors, {INVENTED}].</conclusion>"
)
ANSWER_PROBE = (
    "<thought_log>The revenue was $94 billion [SOURCE: Apple, N/A].</thought_log>\n"
    "<conclusion>The revenue for Apple's last quarter was $94 billion [SOURCE: Apple, N/A].</conclusion>"
)


def _claims(text):
    thought, conclusion = text.split("<conclusion>")
    return extract_claims_from_response(thought, conclusion)


def _no_fetch(url):
    raise AssertionError(f"fetched {url}; a citation the agent never retrieved must not be fetched")


class TestUrlHelpers(unittest.TestCase):

    def test_normalize_ignores_scheme_www_slash_fragment(self):
        self.assertEqual(normalize_url("https://www.Example.com/a/b/#x"),
                         normalize_url("http://example.com/a/b"))

    def test_normalize_keeps_path_and_query(self):
        self.assertNotEqual(normalize_url("https://a.com/x"), normalize_url("https://a.com/y"))
        self.assertNotEqual(normalize_url("https://a.com/x?id=1"), normalize_url("https://a.com/x?id=2"))

    def test_urls_in_text_strips_brackets_and_punctuation(self):
        self.assertEqual(urls_in_text("see https://a.com/b. and [x, https://c.org/d]", None),
                         {"https://a.com/b", "https://c.org/d"})


class TestRetrievedFirst(unittest.TestCase):

    def test_the_invented_url_from_run_03bd3ca5_is_not_retrieved_and_not_fetched(self):
        with patch("validation.verification._fetch_with_status", side_effect=_no_fetch):
            claims, rate = verify_claims(_claims(ANSWER_INVENTED), [REAL, "https://www.macrumors.com/2026/07/30/apple-3q-2026-earnings"])
        cite = next(c for c in claims if c.claim_type == "citation")
        self.assertEqual((cite.verified, cite.check), (False, CHECK_NOT_RETRIEVED))
        self.assertEqual(rate, 0.0)

    def test_a_retrieved_url_is_fetched_and_confirmed(self):
        text = ANSWER_INVENTED.replace(INVENTED, REAL)
        with patch("validation.verification._fetch_with_status",
                   return_value=("Apple had revenue of $109.42B in the quarter", None)):
            claims, rate = verify_claims(_claims(text), ["https://www.stockanalysis.com/stocks/aapl/revenue/"])
        cite = next(c for c in claims if c.claim_type == "citation")
        self.assertEqual((cite.verified, cite.check), (True, CHECK_CONFIRMED))
        self.assertEqual(rate, 1.0)

    def test_a_404_is_a_missing_page_not_couldnt_check(self):
        with patch("validation.verification._fetch_with_status", return_value=(None, 404)):
            claims, _ = verify_claims(_claims(ANSWER_INVENTED))  # retrieved URLs unknown
        cite = next(c for c in claims if c.claim_type == "citation")
        self.assertEqual((cite.verified, cite.check), (False, CHECK_PAGE_MISSING))

    def test_a_blocked_site_is_still_couldnt_check(self):
        for status in (403, None):
            with patch("validation.verification._fetch_with_status", return_value=(None, status)):
                claims, _ = verify_claims(_claims(ANSWER_INVENTED))
            cite = next(c for c in claims if c.claim_type == "citation")
            self.assertEqual((cite.verified, cite.check), (None, CHECK_UNREACHABLE), status)

    def test_an_agent_that_saw_no_urls_cannot_cite_one(self):
        with patch("validation.verification._fetch_with_status", side_effect=_no_fetch):
            claims, _ = verify_claims(_claims(ANSWER_INVENTED), [])
        self.assertEqual(next(c for c in claims if c.claim_type == "citation").check, CHECK_NOT_RETRIEVED)

    def test_check_is_in_the_stored_claim(self):
        with patch("validation.verification._fetch_with_status", side_effect=_no_fetch):
            claims, _ = verify_claims(_claims(ANSWER_INVENTED), [])
        self.assertEqual(next(c for c in claims if c.claim_type == "citation").to_dict()["check"],
                         "not_retrieved")


class TestCheckPenalties(unittest.TestCase):

    def setUp(self):
        from web.server import _check_penalties
        self.penalties = _check_penalties

    def test_run_03bd3ca5_gets_all_three(self):
        claims = [{"claim_type": "citation", "verified": False, "check": "not_retrieved"}]
        consistency = {"agreement": "LOW", "number_divergence_flag": True,
                       "probe_halted": False, "probe_error": None}
        found = self.penalties(claims, consistency)
        self.assertEqual(len(found), 3)
        self.assertAlmostEqual(sum(x["penalty"] for x in found), 0.65)

    def test_a_clean_run_loses_nothing(self):
        claims = [{"claim_type": "citation", "verified": True, "check": "confirmed"}]
        self.assertEqual(self.penalties(claims, {"agreement": "HIGH", "number_divergence_flag": False}), [])

    def test_a_probe_that_failed_says_nothing(self):
        self.assertEqual(self.penalties([], {"agreement": "LOW", "number_divergence_flag": True,
                                             "probe_halted": True}), [])

    def test_unchecked_citations_alone_do_not_count_as_unconfirmed(self):
        # N/A citations carry no `check`: nothing was fetched, so nothing failed to confirm.
        self.assertEqual(self.penalties([{"claim_type": "citation", "verified": None}], None), [])


class TestChatRouteReplaysRun03bd3ca5(unittest.TestCase):
    """The chat route end to end, with run 03bd3ca5's shape: one search, an invented URL, a probe that disagrees."""

    def setUp(self):
        from core.parsing import _parse_response
        from tests.support import no_model_extraction

        calls = {"n": 0}

        def build(cfg):
            emit = cfg.get("_on_tool_event")

            def adapter(subject, context, directive):
                calls["n"] += 1
                if emit:
                    emit({"seq": 1, "tool": "tavily_search", "phase": "finished", "status": "ok",
                          "query": "Apples revenue last quarter", "urls": [REAL], "results": []})
                return _parse_response(ANSWER_INVENTED if calls["n"] == 1 else ANSWER_PROBE)
            return adapter

        self._tmpdir = tempfile.mkdtemp()
        self._patchers = [
            patch("web.db.DB_PATH", Path(self._tmpdir) / "test.db"),
            patch("web.server._build_adapter", side_effect=build),
            patch("validation.verification._fetch_with_status", side_effect=_no_fetch),
            no_model_extraction(),
        ]
        for p in self._patchers:
            p.start()
        from fastapi.testclient import TestClient
        from web.db import init_db
        from web.server import app
        init_db()
        self.client = TestClient(app)

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_invented_citation_and_disagreement_lower_confidence(self):
        token = self.client.post("/api/auth/token", json={"scope": "auditor"}, headers=ADMIN_HEADERS).json()["access_token"]
        run = self.client.post("/api/chat", json={"message": "What was Apples revenue last quarter?", "context": ""},
                               headers={"Authorization": f"Bearer {token}"}).json()
        cite = next(c for c in run["claims"] if c["claim_type"] == "citation")
        self.assertEqual(cite["check"], "not_retrieved")
        self.assertTrue(run["consistency"]["number_divergence_flag"])
        self.assertEqual(run["confidence_before_checks"], 0.75)
        self.assertEqual(run["confidence_score"], 0.1)
        self.assertEqual(run["confidence_classification"], "HIGH_UNCERTAINTY_SPECULATIVE")
        self.assertTrue(run["high_uncertainty"])
        self.assertTrue(run["confidence_degraded"])
        self.assertEqual(len(run["confidence_adjustments"]), 3)
        self.assertEqual(run["session"]["run_confidence_score"], 0.1)
        # The attempt keeps the score it was given; the run's score is what the checks left.
        self.assertEqual(run["reasoning_objects"][0]["confidence_score"], 0.75)
        stored = self.client.get(f"/api/runs/{run['run_id']}",
                                 headers={"Authorization": f"Bearer {token}"}).json()
        self.assertEqual(stored["confidence_score"], 0.1)


class TestParseSearchPlan(unittest.TestCase):

    def setUp(self):
        from adapters.langchain_adapter import _parse_search_plan
        self.parse = _parse_search_plan

    def test_json_array_inside_prose(self):
        self.assertEqual(self.parse('Sure:\n["Apple revenue latest", "Apple 10-Q revenue"]'),
                         ["Apple revenue latest", "Apple 10-Q revenue"])

    def test_queries_object(self):
        self.assertEqual(self.parse('{"queries": ["Apple revenue latest"]}'), ["Apple revenue latest"])

    def test_numbered_lines_when_not_json(self):
        self.assertEqual(self.parse("Queries:\n1. Apple revenue\n2) Apple 10-Q\n- \"Apple news\""),
                         ["Apple revenue", "Apple 10-Q", "Apple news"])

    def test_deduped_and_capped(self):
        from adapters.langchain_adapter import _MAX_PLANNED_SEARCHES
        plan = self.parse('["q one", "Q ONE", "q two", "q three", "q four", "q five"]')
        self.assertEqual(len(plan), _MAX_PLANNED_SEARCHES)
        self.assertEqual(plan[:2], ["q one", "q two"])

    def test_nothing_usable(self):
        self.assertEqual(self.parse(""), [])
        self.assertEqual(self.parse("[]"), [])


class TestFormatSearchResults(unittest.TestCase):

    def test_each_search_represented_each_url_once_url_beside_text(self):
        from adapters.langchain_adapter import _format_search_results
        a = {"results": [{"url": "https://a/1", "title": "A1", "content": "alpha"},
                         {"url": "https://a/2", "title": "A2", "content": "beta"}]}
        b = {"results": [{"url": "https://a/1", "title": "A1", "content": "alpha"},
                         {"url": "https://b/1", "title": "B1", "content": "gamma"}]}
        text = _format_search_results([("qa", a), ("qb", b)])
        self.assertEqual(text.count("https://a/1"), 1)
        self.assertLess(text.index("https://b/1"), text.index("https://a/2"))  # round-robin
        self.assertIn("A1\n  URL: https://a/1", text)

    def test_stays_within_the_limit(self):
        from adapters.langchain_adapter import SEARCH_RESULTS_CHAR_LIMIT, _format_search_results
        big = {"results": [{"url": f"https://x/{i}", "title": "t", "content": "y" * 2000} for i in range(40)]}
        self.assertLessEqual(len(_format_search_results([("q", big)])), SEARCH_RESULTS_CHAR_LIMIT + 200)

    def test_errors_and_empty_results(self):
        from adapters.langchain_adapter import _format_search_results
        self.assertEqual(_format_search_results([("q", "Tool error: 400")]),
                         "The planned searches returned no results.")


class _FakeSearch:
    name = "tavily_search"
    queries: list = []

    def __init__(self, **kwargs):
        pass

    async def ainvoke(self, args):
        _FakeSearch.queries.append(args["query"])
        n = len(_FakeSearch.queries)
        return {"results": [{"url": f"https://source{n}.example/page", "title": f"S{n}",
                             "content": f"Revenue was ${100 + n}B."}]}


GOOD_REPLY = "<thought_log>t</thought_log>\n<conclusion>c</conclusion>"


class _FakeChat:
    """Plans and answers with invoke(); records whether a tool was ever bound."""

    def __init__(self, plan_reply, answers=None):
        self.plan_reply = plan_reply
        self.answers = list(answers or [GOOD_REPLY])
        self.answer_prompt = None
        self.tools_bound = False

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        if messages[0].content.startswith("You plan web searches"):
            return AIMessage(content=self.plan_reply)
        self.answer_prompt = messages[1].content
        return AIMessage(content=self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])

    def bind_tools(self, tools):
        self.tools_bound = True
        return self

    async def ainvoke(self, messages):
        from langchain_core.messages import AIMessage
        self.answer_prompt = messages[1].content
        return AIMessage(content=self.answers[0])


@needs_langchain
class TestPlanThenSearch(unittest.TestCase):

    def _run(self, plan_reply, context=""):
        from adapters.langchain_adapter import make_langchain_adapter
        from core.directive import DirectiveVersion

        _FakeSearch.queries = []
        chat = _FakeChat(plan_reply)
        events = []
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test"}), \
             patch("adapters.langchain_adapter._build_chat", return_value=chat), \
             patch("langchain_tavily.TavilySearch", _FakeSearch):
            adapter = make_langchain_adapter(on_tool_event=events.append)
            adapter("What was Apples revenue last quarter?", context,
                    DirectiveVersion(version="test", text="directive"))
        return chat, events

    def test_every_planned_search_runs_and_feeds_the_context(self):
        chat, events = self._run('["Apple revenue latest quarter", "Apple earnings press release", "Apple 10-Q revenue"]')
        self.assertEqual(_FakeSearch.queries,
                         ["Apple revenue latest quarter", "Apple earnings press release", "Apple 10-Q revenue"])
        plan = events[0]
        self.assertEqual((plan["tool"], plan["source"], len(plan["queries"])), ("search_plan", "model", 3))
        finished = [e for e in events if e.get("phase") == "finished"]
        self.assertEqual([e["urls"] for e in finished],
                         [["https://source1.example/page"], ["https://source2.example/page"],
                          ["https://source3.example/page"]])
        self.assertEqual(len({e["seq"] for e in events}), 4)  # plan + three searches, no reused seq
        for n in (1, 2, 3):
            self.assertIn(f"https://source{n}.example/page", chat.answer_prompt)
        self.assertIn("Never pair one result's site name with another result's URL", chat.answer_prompt)
        # The answer is one call with no tool bound (run 72cfce56 lost its format mid-loop).
        self.assertFalse(chat.tools_bound)
        self.assertNotIn("You have a search tool available", chat.answer_prompt)

    def test_a_retry_reuses_the_searches(self):
        from adapters.langchain_adapter import make_langchain_adapter
        from core.directive import DirectiveVersion
        from core.parsing import StructuralParseError

        _FakeSearch.queries = []
        chat = _FakeChat('["q one", "q two"]', answers=["no blocks", GOOD_REPLY])
        events = []
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test"}), \
             patch("adapters.langchain_adapter._build_chat", return_value=chat), \
             patch("langchain_tavily.TavilySearch", _FakeSearch):
            adapter = make_langchain_adapter(on_tool_event=events.append)
            directive = DirectiveVersion(version="test", text="directive")
            with self.assertRaises(StructuralParseError):
                adapter("Q?", "", directive)
            adapter("Q?", "", directive)
        self.assertEqual(_FakeSearch.queries, ["q one", "q two"])  # searched once, not twice
        plans = [e for e in events if e.get("tool") == "search_plan"]
        self.assertEqual([p["source"] for p in plans], ["model", "reused"])
        self.assertIn("https://source2.example/page", chat.answer_prompt)

    def test_unreadable_plan_searches_the_question_and_says_so(self):
        chat, events = self._run("I cannot help with that.")
        # One line of prose is still read as a query; an empty reply is the real fallback.
        self.assertEqual(len(_FakeSearch.queries), 1)
        chat, events = self._run("")
        self.assertEqual(_FakeSearch.queries, ["What was Apples revenue last quarter?"])
        self.assertEqual(events[0]["source"], "fallback")

    def test_nothing_is_planned_when_context_is_given(self):
        chat, events = self._run('["unused"]', context="Revenue was $1B [SOURCE: 10-Q, N/A].")
        self.assertEqual(_FakeSearch.queries, [])
        self.assertFalse(any(e.get("tool") == "search_plan" for e in events))


@needs_langchain
class TestPlanStepInTrace(unittest.TestCase):

    def test_plan_becomes_a_plan_step(self):
        from web.server import _tool_event_recorder
        from web.step_trace import PHASE_CHAT, StepTrace

        trace = StepTrace()
        _tool_event_recorder(trace, PHASE_CHAT)({
            "seq": 1, "tool": "search_plan", "phase": "planned", "status": "ok",
            "queries": ["a b", "c d"], "source": "model", "raw": '["a b", "c d"]', "duration_ms": 5.0})
        step = trace.to_list()[0]
        self.assertEqual((step["kind"], step["label"], step["queries"]),
                         ("plan", "Search plan: 2 searches", ["a b", "c d"]))

    def test_a_reused_plan_says_it_did_not_search_again(self):
        from web.server import _tool_event_recorder
        from web.step_trace import PHASE_CHAT, StepTrace

        trace = StepTrace()
        _tool_event_recorder(trace, PHASE_CHAT)({
            "seq": 1, "tool": "search_plan", "phase": "planned", "status": "ok",
            "queries": ["a b"], "source": "reused", "duration_ms": 0.0})
        self.assertEqual(trace.to_list()[0]["label"],
                         "Search plan: reusing the 1 search above, not searching again")



RESULTS = "- A page\n  URL: https://a/1"


class TestWhatTheAgentWasSent(unittest.TestCase):
    """Fix 4 (2026-10-06): the record of an attempt includes the search results added to its prompt."""

    def _loop(self, adapter):
        import uuid
        from core.schemas import AgentID
        from pipeline.middleware import run_validation_loop
        return run_validation_loop("Q?", "", uuid.uuid4(), AgentID.EXTERNAL, call_agent_fn=adapter)

    def test_a_successful_attempt_records_the_results(self):
        import dataclasses
        from core.parsing import _parse_response

        def adapter(subject, context, directive):
            return dataclasses.replace(_parse_response(GOOD_REPLY), retrieved_context=RESULTS)
        result = self._loop(adapter)
        window = result.reasoning_objects[0].context_window
        self.assertEqual(window, {"subject": "Q?", "context": "", "retrieved": RESULTS})

    def test_a_failed_attempt_records_them_too(self):
        from core.parsing import StructuralParseError
        from pipeline.middleware import HaltError

        def adapter(subject, context, directive):
            raise StructuralParseError("Response missing <conclusion> block", "x", retrieved_context="results")
        with self.assertRaises(HaltError) as ctx:
            self._loop(adapter)
        self.assertEqual([o.context_window.get("retrieved") for o in ctx.exception.reasoning_objects],
                         ["results", "results"])

    def test_an_adapter_that_adds_nothing_records_nothing_extra(self):
        from core.parsing import _parse_response
        result = self._loop(lambda s, c, d: _parse_response(GOOD_REPLY))
        self.assertNotIn("retrieved", result.reasoning_objects[0].context_window)


@needs_langchain
class TestAdapterReportsWhatItAdded(unittest.TestCase):

    def test_success_and_failure_both_carry_the_results(self):
        from adapters.langchain_adapter import make_langchain_adapter
        from core.directive import DirectiveVersion
        from core.parsing import StructuralParseError

        _FakeSearch.queries = []
        chat = _FakeChat('["q one"]', answers=["no blocks", GOOD_REPLY])
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test"}),              patch("adapters.langchain_adapter._build_chat", return_value=chat),              patch("langchain_tavily.TavilySearch", _FakeSearch):
            adapter = make_langchain_adapter()
            directive = DirectiveVersion(version="test", text="directive")
            with self.assertRaises(StructuralParseError) as ctx:
                adapter("Q?", "", directive)
            response = adapter("Q?", "", directive)
        self.assertIn("https://source1.example/page", ctx.exception.retrieved_context)
        self.assertEqual(response.retrieved_context, ctx.exception.retrieved_context)

    def test_with_context_given_nothing_is_added(self):
        from adapters.langchain_adapter import make_langchain_adapter
        from core.directive import DirectiveVersion

        chat = _FakeChat('["unused"]')
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test"}),              patch("adapters.langchain_adapter._build_chat", return_value=chat),              patch("langchain_tavily.TavilySearch", _FakeSearch):
            response = make_langchain_adapter()("Q?", "Revenue was $1B.", DirectiveVersion(version="t", text="d"))
        self.assertIsNone(response.retrieved_context)


if __name__ == "__main__":
    unittest.main()
