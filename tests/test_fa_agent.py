"""
The built-in financial agent, driven by scripted model turns (financial_agent/agent.py, PLAN.md phase 2).

Network-free and model-free: the model is a list of scripted turns, the data is the recorded AAPL
fixtures. This pins the control flow and the record, and says nothing about how a real model
behaves; that is measured separately and not claimed here.

  - plan_request: ticker and horizon from free text, the default horizon labelled as one.
  - parse_analysis_response: the three-block reply, strict JSON, vocabulary never coerced.
  - run_analysis: success; a broken first reply that recovers; two broken replies that halt with
    every attempt kept; a bad tool call that comes back as a recorded error, not a result;
    a model that never stops asking for data.
  - the routes: stream, get, list, and who may call them.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.analysis import DEFAULT_HORIZON
from core.parsing import StructuralParseError
from financial_agent import agent as A
from financial_agent import prompts
from financial_agent.bundle import build_bundle, filing_snapshot
from financial_agent.prices import snapshot_from_payloads
from financial_agent.replay import check_recorded_calls
from tests.support import ADMIN_HEADERS
from tests.test_concurrency_and_stream import _parse_sse

FIX = Path(__file__).parent / "fixtures"


def _bundle():
    daily = json.loads((FIX / "av_aapl_daily_compact_2026-10-05.json").read_text(encoding="utf-8"))
    weekly = json.loads((FIX / "av_aapl_weekly_2026-10-05_trimmed.json").read_text(encoding="utf-8"))
    prices = snapshot_from_payloads("AAPL", daily, weekly, fetched_at="2026-10-06T01:00:00Z", urls=("d", "w"))
    facts = json.loads((FIX / "edgar_aapl_companyfacts_sample.json").read_text(encoding="utf-8"))
    return build_bundle(ticker="AAPL", prices=prices, filing=filing_snapshot("AAPL", facts, "0000320193"),
                        created_at="2026-10-06T01:00:00Z")


SIGNAL = {"signal": "buy", "horizon": "3-6m", "conviction": "medium",
          "key_points": [{"text": "The standard indicator panel is mixed to positive.", "evidence": [{"call": 2}]}],
          "cited_figures": [{"name": "rsi_14", "value": 58.2, "unit": "index", "ref": {"call": 2}}]}


def reply(signal=None, *, thought="I looked at the indicator panel from call 2 and the price history.",
          conclusion="On this data the signal is a buy over three to six months, at medium conviction."):
    sig = SIGNAL if signal is None else signal
    body = sig if isinstance(sig, str) else json.dumps(sig)
    return f"<thought_log>{thought}</thought_log>\n<conclusion>{conclusion}</conclusion>\n<signal>{body}</signal>"


def call(name, args=None, id_="c"):
    return {"id": id_, "name": name, "args": args or {}}


class Scripted:
    """A ModelStep that plays a list of turns, and remembers what it was shown."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen: list[list[dict]] = []

    def __call__(self, messages):
        self.seen.append([dict(m) for m in messages])
        if not self.turns:
            raise AssertionError("the script ran out of turns")
        turn = self.turns.pop(0)
        return {"content": turn, "tool_calls": []} if isinstance(turn, str) else turn


def good_run():
    """describe_data, the indicator panel, then a valid reply."""
    return [{"content": "", "tool_calls": [call("describe_data", id_="c1")]},
            {"content": "", "tool_calls": [call("get_indicators", id_="c2")]},
            reply()]


class TestPlanRequest(unittest.TestCase):

    def test_a_ticker_and_the_default_horizon_which_is_labelled_as_one(self):
        plan = A.plan_request("Should I buy AAPL?")
        self.assertEqual((plan["ticker"], plan["horizon"], plan["horizon_defaulted"]), ("AAPL", DEFAULT_HORIZON, True))

    def test_a_named_horizon_overrides_the_default(self):
        for text, want in [("AAPL over the next 6 months", "3-6m"), ("AAPL for 2 weeks", "1-3m"),
                           ("AAPL in 3 to 6 months", "3-6m"), ("AAPL for a 2 year hold", "12m+"),
                           ("AAPL long-term", "12m+"), ("AAPL short term", "1-3m"),
                           ("AAPL for the next year", "6-12m"), ("AAPL over 9 months", "6-12m")]:
            plan = A.plan_request(text)
            self.assertEqual((plan["horizon"], plan["horizon_defaulted"]), (want, False), text)

    def test_a_company_name_with_a_capital_letter_works(self):
        self.assertEqual(A.plan_request("What about Apple stock?")["ticker"], "AAPL")

    def test_no_company_is_a_request_error_that_says_what_to_change(self):
        with self.assertRaises(A.RequestError) as ctx:
            A.plan_request("tell me a joke")
        self.assertIn("ticker", str(ctx.exception))

    def test_empty_and_overlong_messages_are_refused(self):
        for text in ("", "   ", "AAPL " + "x" * A.MAX_MESSAGE_CHARS):
            with self.assertRaises(A.RequestError):
                A.plan_request(text)


class TestParseResponse(unittest.TestCase):

    def test_a_good_reply_gives_the_blocks_and_a_checked_signal(self):
        response, parsed = A.parse_analysis_response(reply())
        self.assertEqual(parsed.status, "valid")
        self.assertEqual(parsed.signal["signal"], "buy")
        self.assertIn("buy", response.conclusion)
        self.assertIn("call 2", response.thought_log)

    def test_each_broken_reply_names_the_rule_it_broke(self):
        good = reply()
        cases = {
            "none of the": "I think you should buy.",
            "no <thought_log>": good.replace("<thought_log>", "").replace("</thought_log>", ""),
            "no <conclusion>": good.replace("<conclusion>", "").replace("</conclusion>", ""),
            "no <signal>": good.split("<signal>")[0],
            "outside the three blocks": "Sure, here you go:\n" + good,
            "not valid JSON": reply('{"signal": "buy",}'),
            "not valid JSON ": reply("```json\n" + json.dumps(SIGNAL) + "\n```"),
            "is invalid": reply({"signal": "Buy"}),
        }
        for fragment, raw in cases.items():
            with self.assertRaises(StructuralParseError, msg=fragment) as ctx:
                A.parse_analysis_response(raw)
            self.assertIn(fragment.strip(), str(ctx.exception))
            self.assertEqual(ctx.exception.raw_response, raw)

    def test_a_signal_outside_the_vocabulary_is_never_coerced(self):
        for bad in ("Buy", "A", "strong buy", "bullish", None, 3):
            with self.assertRaises(StructuralParseError):
                A.parse_analysis_response(reply({"signal": bad}))

    def test_the_signal_must_come_directly_after_the_conclusion(self):
        raw = "<thought_log>t</thought_log>\n<signal>" + json.dumps(SIGNAL) + "</signal>\n<conclusion>c</conclusion>"
        with self.assertRaises(StructuralParseError):
            A.parse_analysis_response(raw)

    def test_a_partly_valid_signal_keeps_what_is_valid_and_lists_what_was_dropped(self):
        _, parsed = A.parse_analysis_response(reply({**SIGNAL, "conviction": "certain"}))
        self.assertEqual(parsed.status, "partial")
        self.assertNotIn("conviction", parsed.signal)
        self.assertTrue(any("conviction" in i for i in parsed.issues))


class TestRunAnalysis(unittest.TestCase):

    def setUp(self):
        self.bundle = _bundle()
        self.request = A.plan_request("Should I buy AAPL over the next 6 months?")

    def run_it(self, turns, **kw):
        step = Scripted(turns)
        events: list[tuple[str, dict]] = []
        record = A.run_analysis(self.request, bundle=self.bundle, model_step=step, model="llama3.2",
                                emit=lambda e, d: events.append((e, d)), **kw)
        return record, step, events

    def test_a_good_run_is_an_ok_record_with_its_signal_and_every_tool_call(self):
        record, step, _ = self.run_it(good_run())
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["output"]["signal"], "buy")
        self.assertEqual(record["schema"], "analysis-v1")
        self.assertEqual(record["bundle_hash"], self.bundle["bundle_hash"])
        self.assertEqual([c["tool"] for c in record["tool_calls"]], ["describe_data", "get_indicators"])
        self.assertEqual([c["seq"] for c in record["tool_calls"]], [1, 2])
        self.assertTrue(all(c["slot"] == "a" for c in record["tool_calls"]))
        self.assertEqual(record["agent"]["kind"], "builtin")
        self.assertEqual(record["agent"]["prompt_version"], prompts.ANALYSIS_PROMPT_VERSION)
        self.assertEqual(record["agent"]["reasoning"], "none")
        self.assertTrue(record["inputs_captured"])
        self.assertEqual([a["parse_status"] for a in record["attempts"]], ["SUCCESS"])
        self.assertIn("buy", record["conclusion"])
        self.assertEqual(record["request"]["horizon"], "3-6m")
        self.assertFalse(record["request"]["horizon_defaulted"])

    def test_every_recorded_call_is_reproducible_from_the_bundle(self):
        record, _, _ = self.run_it(good_run())
        self.assertEqual(check_recorded_calls(self.bundle, record["tool_calls"]), [])

    def test_the_model_is_shown_the_call_number_with_each_result(self):
        _, step, _ = self.run_it(good_run())
        tool_messages = [m for m in step.seen[-1] if m["role"] == "tool"]
        self.assertEqual([json.loads(m["content"])["call"] for m in tool_messages], [1, 2])
        self.assertEqual(json.loads(tool_messages[0]["content"])["tool"], "describe_data")

    def test_the_prompt_carries_the_directive_the_ticker_and_the_horizon(self):
        _, step, _ = self.run_it(good_run())
        system, user = step.seen[0][0], step.seen[0][1]
        self.assertEqual(system["content"], prompts.ANALYSIS_DIRECTIVE)
        self.assertIn("Ticker: AAPL", user["content"])
        self.assertIn("named by the user", user["content"])
        self.assertIn(self.bundle["bundle_hash"][:19], user["content"])

    def test_steps_are_numbered_in_order_and_sent_as_they_happen(self):
        record, _, events = self.run_it(good_run())
        self.assertEqual([s["seq"] for s in record["steps"]], list(range(1, len(record["steps"]) + 1)))
        self.assertEqual([s["kind"] for s in record["steps"]], ["tool", "tool", "llm", "result"])
        self.assertEqual([e for e, _ in events], ["step"] * len(record["steps"]))

    def test_a_broken_first_reply_recovers_on_the_second_and_both_attempts_are_kept(self):
        record, step, _ = self.run_it(["Sure, I think a buy.", *good_run()])
        self.assertEqual(record["status"], "ok")
        self.assertEqual([a["parse_status"] for a in record["attempts"]], ["PARSE_FAILURE", "SUCCESS"])
        self.assertEqual(record["attempts"][0]["raw_text"], "Sure, I think a buy.")
        self.assertIn("none of the", record["corrective_text"])
        retry_prompt = [m for m in step.seen[1] if m["role"] == "user"][0]["content"]
        self.assertIn("Your previous reply failed validation", retry_prompt)
        statuses = [s["status"] for s in record["steps"] if s["kind"] == "llm"]
        self.assertEqual(statuses, ["parse_failure", "ok"])

    def test_two_broken_replies_halt_with_every_attempt_kept_and_no_signal(self):
        record, _, _ = self.run_it(["not the format", reply({"signal": "maybe"})])
        self.assertEqual(record["status"], "halted")
        self.assertIsNone(record["output"])
        self.assertIsNone(record["conclusion"])
        self.assertEqual([a["parse_status"] for a in record["attempts"]], ["PARSE_FAILURE", "HALT"])
        self.assertEqual(record["attempts"][0]["raw_text"], "not the format")
        self.assertIn("maybe", record["attempts"][1]["raw_text"])
        self.assertTrue(record["halt_reason"])
        self.assertEqual(record["steps"][-1]["status"], "halt")

    def test_a_reply_that_restates_the_directive_fails_like_any_other_broken_reply(self):
        echo = prompts.ANALYSIS_DIRECTIVE.split("\n\n")[1]
        record, _, _ = self.run_it([reply(conclusion=echo), reply(conclusion=echo)])
        self.assertEqual(record["status"], "halted")

    def test_a_bad_tool_call_comes_back_as_a_recorded_error_never_a_result(self):
        turns = [{"content": "", "tool_calls": [call("get_prices", {"last": 99999}, "c1"),
                                                call("not_a_tool", {}, "c2")]}, *good_run()[1:]]
        record, step, _ = self.run_it(turns)
        first, second = record["tool_calls"][0], record["tool_calls"][1]
        self.assertIn("error", first["result"])
        self.assertIn("error", second["result"])
        self.assertEqual([s["status"] for s in record["steps"] if s["kind"] == "tool"][:2], ["error", "error"])
        self.assertEqual(record["status"], "ok")

    def test_a_model_that_never_stops_asking_for_data_is_cut_off_and_goes_through_retry_and_halt(self):
        forever = {"content": "", "tool_calls": [call("describe_data")]}
        record, step, _ = self.run_it([forever] * 4, max_rounds=2)
        self.assertEqual(record["status"], "halted")
        self.assertEqual(len(step.seen), 4, "two rounds per attempt, two attempts")
        self.assertEqual(len(record["tool_calls"]), 4)
        self.assertIn("still asking for data", record["steps"][-2]["detail"])

    def test_no_horizon_in_the_signal_shows_the_requests_and_says_so(self):
        no_horizon = {k: v for k, v in SIGNAL.items() if k != "horizon"}
        record, _, _ = self.run_it([reply(no_horizon)])
        self.assertEqual(record["output"]["horizon"], "3-6m")
        self.assertTrue(any("horizon" in i for i in record["issues"]))

    def test_a_dropped_field_is_reported_not_hidden(self):
        record, _, _ = self.run_it([reply({**SIGNAL, "conviction": "certain"})])
        self.assertNotIn("conviction", record["output"])
        self.assertTrue(any("conviction" in i for i in record["issues"]))

    def test_a_model_that_cannot_be_reached_raises_and_records_nothing(self):
        def down(messages):
            raise ConnectionError("model server down")
        with self.assertRaises(ConnectionError):
            A.run_analysis(self.request, bundle=self.bundle, model_step=down, model="llama3.2")

    def test_the_record_is_json_serialisable(self):
        record, _, _ = self.run_it(good_run())
        self.assertEqual(json.loads(json.dumps(record)), record)

    def test_reasoning_mode_is_native_only_for_models_known_to_think(self):
        self.assertEqual(A.reasoning_mode("llama3.2"), "none")
        self.assertEqual(A.reasoning_mode("qwen2.5:7b"), "none")
        self.assertEqual(A.reasoning_mode("deepseek-r1:8b"), "native")
        self.assertEqual(A.reasoning_mode("langchain:gemini-2.5-flash"), "native")


class TestPrepareBundle(unittest.TestCase):

    def test_missing_filing_data_is_a_note_not_a_failure(self):
        prices = _bundle()["data"]["prices"]

        def no_filing(ticker):
            raise RuntimeError("EDGAR unreachable")

        bundle, notes = A.prepare_bundle("AAPL", prices_fn=lambda t: prices, filing_fn=no_filing)
        self.assertIsNone(bundle["data"]["filing"])
        self.assertIn("EDGAR unreachable", notes[0])

    def test_missing_prices_propagate_with_their_reason(self):
        from financial_agent.prices import PriceFetchError

        def no_prices(ticker):
            raise PriceFetchError("no_key", "ALPHAVANTAGE_API_KEY is not set")

        with self.assertRaises(PriceFetchError):
            A.prepare_bundle("AAPL", prices_fn=no_prices, filing_fn=lambda t: None)


class TestToolSchemas(unittest.TestCase):

    def test_every_data_tool_has_a_schema_and_nothing_else_does(self):
        from financial_agent.tools import TOOLS
        names = [s["function"]["name"] for s in A.tool_schemas()]
        self.assertEqual(names, list(TOOLS))
        for s in A.tool_schemas():
            self.assertEqual(s["function"]["parameters"]["type"], "object")


class _Routes(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db = patch("web.db.DB_PATH", Path(self._tmpdir) / "t.db")
        self._db.start()
        from web import db
        db.init_db()
        self.db = db
        self.bundle = _bundle()
        self._patches = [
            patch("web.server._prepare_bundle", side_effect=lambda ticker: (self.bundle, [])),
            patch.dict("os.environ", {"ALPHAVANTAGE_API_KEY": "test-key"}),
        ]
        for p in self._patches:
            p.start()
        self.script = None
        from web.server import app
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._db.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def use_script(self, turns):
        self.script = Scripted(turns)
        p = patch("web.server._build_analysis_step", side_effect=lambda cfg: self.script)
        p.start()
        self.addCleanup(p.stop)

    def headers(self, scope):
        token = self.client.post("/api/auth/token", json={"scope": scope}, headers=ADMIN_HEADERS).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def stream(self, scope, message="Should I buy AAPL?"):
        with self.client.stream("POST", "/api/analysis/stream", json={"message": message},
                                headers=self.headers(scope)) as resp:
            self.assertEqual(resp.status_code, 200)
            return _parse_sse("".join(resp.iter_text()))


class TestAnalysisRoutes(_Routes):

    def test_the_stream_sends_steps_then_the_stored_record(self):
        self.use_script(good_run())
        events = self.stream("auditor")
        names = [e for e, _ in events]
        self.assertEqual(names[0], "run_started")
        self.assertEqual(names[-1], "result")
        self.assertIn("step", names)
        self.assertEqual(events[0][1]["mode"], "analysis")
        record = events[-1][1]
        self.assertEqual(record["status"], "ok")
        self.assertEqual(self.db.get_analysis(record["run_id"])["output"]["signal"], "buy")
        self.assertIsNotNone(self.db.get_bundle(self.bundle["bundle_hash"]))

    def test_a_halted_run_is_stored_too_with_every_attempt(self):
        self.use_script(["nope", "still nope"])
        record = self.stream("auditor")[-1][1]
        self.assertEqual(record["status"], "halted")
        stored = self.db.get_analysis(record["run_id"])
        self.assertEqual([a["parse_status"] for a in stored["attempts"]], ["PARSE_FAILURE", "HALT"])

    def test_get_and_list(self):
        self.use_script(good_run())
        run_id = self.stream("auditor")[-1][1]["run_id"]
        got = self.client.get(f"/api/analysis/{run_id}", headers=self.headers("auditor"))
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["run_id"], run_id)
        listed = self.client.get("/api/analysis", headers=self.headers("auditor")).json()
        self.assertEqual([r["run_id"] for r in listed], [run_id])
        self.assertNotIn("tool_calls", listed[0])
        self.assertNotIn("thought_log", listed[0])
        self.assertEqual(listed[0]["output"]["signal"], "buy")

    def test_an_unknown_analysis_is_a_404(self):
        self.assertEqual(self.client.get("/api/analysis/nope", headers=self.headers("auditor")).status_code, 404)

    def test_a_request_with_no_company_is_a_422_before_anything_streams(self):
        r = self.client.post("/api/analysis/stream", json={"message": "tell me a joke"}, headers=self.headers("auditor"))
        self.assertEqual(r.status_code, 422)
        self.assertIn("ticker", r.json()["detail"])

    def test_missing_price_data_is_a_503_that_says_why(self):
        with patch.dict("os.environ", {"ALPHAVANTAGE_API_KEY": ""}), \
                patch("financial_agent.prices.DEFAULT_CACHE_DIR", Path(self._tmpdir) / "no-cache"), \
                patch("web.server.unavailable_reason", side_effect=lambda t: __import__("financial_agent.prices", fromlist=["x"]).unavailable_reason(t, cache_dir=Path(self._tmpdir) / "no-cache")):
            r = self.client.post("/api/analysis/stream", json={"message": "Should I buy AAPL?"}, headers=self.headers("auditor"))
        self.assertEqual(r.status_code, 503)
        self.assertIn("ALPHAVANTAGE_API_KEY", r.json()["detail"])

    def test_the_routes_need_a_token(self):
        self.assertEqual(self.client.post("/api/analysis/stream", json={"message": "AAPL"}).status_code, 401)
        self.assertEqual(self.client.get("/api/analysis").status_code, 401)
        self.assertEqual(self.client.get("/api/analysis/x").status_code, 401)

    def test_an_investor_read_withholds_the_thought_log_and_the_raw_replies(self):
        self.use_script(["not the format", *good_run()])
        auditor = self.stream("auditor")[-1][1]
        self.assertTrue(auditor["thought_log"])
        self.assertTrue(auditor["attempts"][0]["raw_text"])
        for body in (self.client.get(f"/api/analysis/{auditor['run_id']}", headers=self.headers("investor")).json(),):
            self.assertIsNone(body["thought_log"])
            self.assertTrue(all(a["raw_text"] is None and a["withheld"] for a in body["attempts"]))
            self.assertIn("thought_log", body["withheld"])
            self.assertEqual(body["output"]["signal"], "buy")
            self.assertIn("buy", body["conclusion"])

    def test_an_investor_stream_result_is_withheld_too(self):
        self.use_script(good_run())
        record = self.stream("investor")[-1][1]
        self.assertIsNone(record["thought_log"])
        self.assertTrue(self.db.get_analysis(record["run_id"])["thought_log"], "stored in full")

    def test_a_data_fetch_failure_ends_the_stream_with_its_reason(self):
        from financial_agent.prices import PriceFetchError

        def boom(ticker):
            raise PriceFetchError("rate_limited", "Alpha Vantage rate limit reached")

        with patch("web.server._prepare_bundle", side_effect=boom):
            events = self.stream("auditor")
        self.assertEqual(events[-1][0], "error")
        self.assertIn("rate limit", events[-1][1]["error"])


if __name__ == "__main__":
    unittest.main()
