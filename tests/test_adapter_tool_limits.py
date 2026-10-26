"""
Two README claims that were marked "in the code; not covered by a test" (Resilience table):

  * An agent loops on tool calls: at most _MAX_TOOL_ITERATIONS (4) model round trips.
  * No search key: the call runs without tools, and the server warns when the input holds a URL.

Network-free and model-free: the chat model and the search tool are replaced by fakes, so
nothing here says anything about how a real model behaves. It pins the control flow.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import unittest
from unittest.mock import patch

HAS_LANGCHAIN = all(importlib.util.find_spec(m) is not None
                    for m in ("langchain_core", "langchain_tavily"))
needs_langchain = unittest.skipUnless(HAS_LANGCHAIN, "langchain-core / langchain-tavily not installed")

ANSWER = ("<thought_log>The context was empty, so there is nothing to ground a claim on.</thought_log>\n"
          "<conclusion>There is not enough context to answer.</conclusion>")


class _FakeSearch:
    """Stands in for langchain_tavily.TavilySearch: records every call, returns one result."""
    calls: list[dict] = []
    name = "tavily_search"

    def __init__(self, *args, **kwargs):
        pass

    async def ainvoke(self, args):
        type(self).calls.append(args)
        return {"results": [{"url": "https://example.com/a", "title": "A", "content": "figure 1"}]}


class _LoopingChat:
    """A model that asks for another search every time it is called, and never answers."""

    def __init__(self):
        self.model_calls = 0
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages):
        from langchain_core.messages import AIMessage
        self.model_calls += 1
        return AIMessage(content=f"searching, turn {self.model_calls}", tool_calls=[
            {"name": "tavily_search", "args": {"query": f"query {self.model_calls}"}, "id": f"call-{self.model_calls}"}])


@needs_langchain
class TestToolLoopCap(unittest.TestCase):

    def setUp(self):
        _FakeSearch.calls = []

    def _run(self, chat, messages=None):
        from adapters import langchain_adapter as L
        events: list[dict] = []
        with patch("langchain_tavily.TavilySearch", _FakeSearch):
            result = asyncio.run(L._invoke_with_tools(chat, messages if messages is not None else [], events.append))
        return result, events

    def test_a_model_that_never_stops_calling_tools_is_cut_off_at_the_cap(self):
        from adapters import langchain_adapter as L
        chat = _LoopingChat()
        result, events = self._run(chat)
        self.assertEqual(L._MAX_TOOL_ITERATIONS, 4)
        self.assertEqual(chat.model_calls, L._MAX_TOOL_ITERATIONS)
        self.assertEqual(len(_FakeSearch.calls), L._MAX_TOOL_ITERATIONS)

    def test_the_last_reply_is_returned_not_raised(self):
        chat = _LoopingChat()
        result, _ = self._run(chat)
        self.assertEqual(result.content, "searching, turn 4")

    def test_every_call_up_to_the_cap_is_traced_as_started_then_finished_in_order(self):
        _, events = self._run(_LoopingChat())
        self.assertEqual([e["phase"] for e in events], ["started", "finished"] * 4)
        self.assertEqual([e["seq"] for e in events if e["phase"] == "started"], [1, 2, 3, 4])
        self.assertEqual([e["query"] for e in events if e["phase"] == "started"],
                         ["query 1", "query 2", "query 3", "query 4"])

    def test_each_search_result_is_handed_back_to_the_model(self):
        from langchain_core.messages import ToolMessage
        messages: list = []
        self._run(_LoopingChat(), messages)
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        self.assertEqual([m.tool_call_id for m in tool_messages], ["call-1", "call-2", "call-3", "call-4"])

    def test_a_model_that_answers_after_one_search_stops_early(self):
        from langchain_core.messages import AIMessage

        class OneSearchThenAnswer(_LoopingChat):
            async def ainvoke(self, messages):
                self.model_calls += 1
                if self.model_calls == 1:
                    return AIMessage(content="", tool_calls=[{"name": "tavily_search", "args": {"query": "q"}, "id": "c1"}])
                return AIMessage(content=ANSWER)

        chat = OneSearchThenAnswer()
        result, _ = self._run(chat)
        self.assertEqual(chat.model_calls, 2)
        self.assertEqual(result.content, ANSWER)


@needs_langchain
class TestNoSearchKey(unittest.TestCase):

    def _adapter_and_chat(self):
        from adapters import langchain_adapter as L

        class PlainChat:
            bound = False

            def bind_tools(self, tools):
                type(self).bound = True
                return self

            def invoke(self, messages):
                from langchain_core.messages import AIMessage
                return AIMessage(content=ANSWER)

        return L, PlainChat

    def test_without_a_key_the_call_runs_with_no_tool_bound(self):
        L, chat_cls = self._adapter_and_chat()
        from core.directive import get_active_directive
        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}), \
                patch.object(L, "_build_chat", return_value=chat_cls()), \
                patch.object(L, "_invoke_with_tools", side_effect=AssertionError("tools must not run without a key")), \
                patch.object(L, "_plan_searches", side_effect=AssertionError("no search plan without a key")):
            response = L.make_langchain_adapter(use_tools=True)("Is the sky blue?", "", get_active_directive())
        self.assertFalse(chat_cls.bound)
        self.assertIn("not enough context", response.conclusion)

    def test_the_server_warns_when_the_input_holds_a_url_and_there_is_no_key(self):
        from web.server import _tool_capability_warning
        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}):
            warning = _tool_capability_warning("langchain", "Check https://example.com/page for the figure")
            self.assertIn("TAVILY_API_KEY", warning)
            self.assertIsNone(_tool_capability_warning("langchain", "No link in here"))
            self.assertIsNone(_tool_capability_warning("langchain", ""))

    def test_with_a_key_the_server_does_not_warn(self):
        from web.server import _tool_capability_warning
        with patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-test"}):
            self.assertIsNone(_tool_capability_warning("langchain", "Check https://example.com/page"))


if __name__ == "__main__":
    unittest.main()
