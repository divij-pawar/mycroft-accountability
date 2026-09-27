"""
B4 — the structured <assessment> block (core/assessment.py, core/parsing.py,
pipeline/middleware.py, directive v1.6.0) and the bull/bear pairing.

Pinned here:
  - the vocabulary is closed and nothing is coerced: "A-" stays an issue, not "A";
  - invalid JSON is never repaired, and says where it broke;
  - the block is optional and never a structural failure under v1.6.0 — but under
    every earlier directive (and the corrective retry) it is still "text outside
    XML blocks": the old contract is unchanged;
  - an assessment is internal tier (SEC-01);
  - v1.6.0 is v1.5.2 plus exactly the listed changes;
  - bull and bear see the same figures and differ only in their brief.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.assessment import KEY_METRICS, expects_assessment, parse_assessment
from core.directive import DIRECTIVE_V1_5_2, DIRECTIVE_V1_6_0, V1_6_0_CHANGES, get_active_directive
from core.parsing import StructuralParseError, _parse_response
from core.schemas import AgentID, ParseStatus
from pipeline.middleware import run_validation_loop

GOOD = {"grade": "BBB", "direction": "hold",
        "assumptions": {"revenue_growth_pct": 8, "margin_trend": "stable", "horizon_months": 12},
        "key_metrics": ["revenue", "net_income"], "key_points": ["Revenue was $109.4 billion."]}

TWO = "<thought_log>\n  Read the Context.\n</thought_log>\n<conclusion>\n  Revenue was $109.4 billion.\n</conclusion>"


def three(block: str) -> str:
    return TWO + "\n" + block


class TestValidator(unittest.TestCase):

    def test_a_complete_block_is_valid(self):
        p = parse_assessment(json.dumps(GOOD), closed=True)
        self.assertEqual((p.status, p.issues), ("valid", []))
        self.assertEqual(p.assessment, GOOD)

    def test_nothing_is_coerced(self):
        p = parse_assessment(json.dumps({**GOOD, "grade": "A-", "direction": "Strong Buy"}), closed=True)
        self.assertEqual(p.status, "partial")
        self.assertNotIn("grade", p.assessment)
        self.assertNotIn("direction", p.assessment)
        self.assertTrue(any("'A-'" in i for i in p.issues))
        self.assertTrue(any("'Strong Buy'" in i for i in p.issues))

    def test_invalid_json_is_not_repaired_and_says_where(self):
        p = parse_assessment('{"grade": "BBB",\n "direction": hold}', closed=True)
        self.assertEqual(p.status, "invalid_json")
        self.assertIsNone(p.assessment)
        self.assertIn("line 2", p.issues[0])
        self.assertEqual(p.raw, '{"grade": "BBB",\n "direction": hold}')

    def test_unclosed_empty_absent(self):
        self.assertEqual(parse_assessment(json.dumps(GOOD), closed=False).status, "unclosed")
        self.assertTrue(parse_assessment(json.dumps(GOOD), closed=False).issues[0].startswith("Found an unclosed"))
        self.assertEqual(parse_assessment("  ", closed=True).status, "empty")
        self.assertEqual(parse_assessment(None, closed=None).status, "absent")

    def test_bounds_and_types(self):
        p = parse_assessment(json.dumps({**GOOD, "key_points": ["a", "b", "c", "d"],
                                         "assumptions": {"revenue_growth_pct": "8%", "discount": 9},
                                         "key_metrics": ["revenue", "vibes"], "rating": "x"}), closed=True)
        self.assertEqual(p.assessment["key_points"], ["a", "b", "c"])
        self.assertNotIn("assumptions", p.assessment)
        self.assertEqual(p.assessment["key_metrics"], ["revenue"])
        joined = " ".join(p.issues)
        for expected in ("only the first 3", "must be a number", "'discount'", "'vibes'", "rating"):
            self.assertIn(expected, joined)
        self.assertEqual(parse_assessment('["BBB"]', closed=True).status, "invalid_fields")

    def test_which_directives_ask(self):
        self.assertTrue(expects_assessment("v1.6.0"))
        self.assertTrue(expects_assessment("v1.10.0"))
        for v in ("v1.5.2", "v1.0.0", "corrective", None, "vX"):
            self.assertFalse(expects_assessment(v))

    def test_key_metrics_track_the_comparators_metric_names(self):
        from validation.facts import METRICS
        self.assertEqual(KEY_METRICS, tuple(m.name for m in METRICS if m.family not in ("self_report", "year")))


class TestParserContract(unittest.TestCase):

    def test_old_contract_unchanged_without_the_flag(self):
        with self.assertRaises(StructuralParseError):
            _parse_response(three(f"<assessment>{json.dumps(GOOD)}</assessment>"))
        r = _parse_response(TWO)
        self.assertIsNone(r.assessment_text)
        self.assertIsNone(r.assessment_closed)

    def test_accepted_closed_unclosed_or_absent_with_the_flag(self):
        r = _parse_response(three(f"<assessment>{json.dumps(GOOD)}</assessment>"), allow_assessment=True)
        self.assertEqual((json.loads(r.assessment_text), r.assessment_closed), (GOOD, True))
        r = _parse_response(three('<assessment>{"grade": "BBB"'), allow_assessment=True)
        self.assertEqual((r.assessment_text, r.assessment_closed), ('{"grade": "BBB"', False))
        r = _parse_response(TWO, allow_assessment=True)
        self.assertEqual((r.assessment_text, r.assessment_closed), (None, False))
        r = _parse_response(TWO + "<assessment>{}</assessment>", allow_assessment=True)  # same line
        self.assertEqual(r.assessment_text, "{}")

    def test_only_directly_after_the_conclusion(self):
        before = f"<thought_log>\n  x\n</thought_log>\n<assessment>{{}}</assessment>\n<conclusion>\n  y\n</conclusion>"
        with self.assertRaises(StructuralParseError):
            _parse_response(before, allow_assessment=True)
        with self.assertRaises(StructuralParseError):
            _parse_response(three("Also, <assessment>{}</assessment>"), allow_assessment=True)


def _adapter(raw_first: str, raw_retry: str | None = None):
    calls = {"n": 0}

    def adapter(subject, context, directive):
        calls["n"] += 1
        raw = raw_first if calls["n"] == 1 else raw_retry
        return _parse_response(raw, allow_assessment=expects_assessment(directive.version))
    return adapter


class TestRecording(unittest.TestCase):

    def _run(self, adapter):
        return run_validation_loop("AAPL", "Revenues: 109417000000.0 USD", uuid.uuid4(), AgentID.BULL,
                                   directive=DIRECTIVE_V1_6_0, call_agent_fn=adapter)

    def test_a_valid_block_is_recorded_internal_tier_only(self):
        ro = self._run(_adapter(three(f"<assessment>{json.dumps(GOOD)}</assessment>"))).reasoning_objects[0]
        self.assertEqual((ro.parse_status, ro.assessment_status, ro.assessment), (ParseStatus.SUCCESS, "valid", GOOD))
        self.assertEqual(ro.to_dict()["assessment"], GOOD)
        investor = ro.to_dict(investor_scope=True)
        for key in ("assessment", "assessment_status", "assessment_issues"):
            self.assertNotIn(key, investor)

    def test_a_broken_block_never_fails_the_attempt(self):
        ro = self._run(_adapter(three('<assessment>{"grade": BBB}</assessment>'))).reasoning_objects[0]
        self.assertEqual((ro.parse_status, ro.attempt_number), (ParseStatus.SUCCESS, 1))
        self.assertEqual(ro.assessment_status, "invalid_json")
        self.assertIsNone(ro.assessment)
        self.assertIn("<assessment>", ro.raw_output["text"])  # the raw block is kept

    def test_absent_is_recorded_as_absent(self):
        ro = self._run(_adapter(TWO)).reasoning_objects[0]
        self.assertEqual((ro.assessment_status, ro.assessment), ("absent", None))

    def test_the_corrective_retry_is_not_asked_for_one(self):
        result = self._run(_adapter("no blocks at all", TWO))
        retry = result.reasoning_objects[1]
        self.assertEqual((retry.parse_status, retry.directive_version), (ParseStatus.SUCCESS, "corrective"))
        self.assertIsNone(retry.assessment_status)

    def test_older_directives_record_nothing(self):
        ro = run_validation_loop("AAPL", "", uuid.uuid4(), AgentID.FINANCIAL, directive=DIRECTIVE_V1_5_2,
                                 call_agent_fn=_adapter(TWO)).reasoning_objects[0]
        self.assertIsNone(ro.assessment_status)


class TestInternalTierInEveryPath(unittest.TestCase):

    def test_redact_for_scope_drops_what_to_dict_drops(self):
        # One list of internal keys must not drift from the other: found 2026-09-26, the
        # investor read of a stored run still carried assessment_status.
        from core.schemas import ReasoningObject
        from validation.gate import redact_for_scope
        ro = ReasoningObject(run_id=uuid.uuid4(), agent_id=AgentID.BULL, attempt_number=1,
                             parse_status=ParseStatus.SUCCESS, confidence_score=0.7, conclusion="x",
                             assessment=GOOD, assessment_status="valid", assessment_issues=("i",))
        internal = set(ro.to_dict()) - set(ro.to_dict(investor_scope=True))
        out = redact_for_scope({"reasoning_objects": [ro.to_dict()]}, "investor", None)
        self.assertEqual(internal & set(out["reasoning_objects"][0]), set())
        self.assertTrue({"assessment", "assessment_status", "assessment_issues"} <= internal)


class TestDirective(unittest.TestCase):

    def test_v160_is_registered_but_not_active_and_changes_nothing_else(self):
        self.assertIs(get_active_directive(), DIRECTIVE_V1_5_2)  # reverted 2026-09-26; see core/directive.py
        text = DIRECTIVE_V1_6_0.text
        for old, new in reversed(V1_6_0_CHANGES):
            self.assertEqual(text.count(new), 1)
            text = text.replace(new, old)
        self.assertEqual(text, DIRECTIVE_V1_5_2.text)

    def test_says_what_the_block_is_and_keeps_the_grounding_rule(self):
        t = DIRECTIVE_V1_6_0.text
        for phrase in ("OPTIONAL ASSESSMENT BLOCK", "AAA, AA, A, BBB, BB, B, CCC", "buy, hold, sell",
                       "otherwise leave it out", "GROUNDING RULE", "CITATION RULE", "insufficient context",
                       "</assessment>"):
            self.assertIn(phrase, t)
        self.assertIn("except the assessment's grade, direction and assumption values", t)


class TestBullBearRoute(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._patches = [
            patch("web.db.DB_PATH", Path(self._tmpdir) / "t.db"),
            patch("web.server.lookup_cik", return_value="0000320193"),
            patch("web.server.fetch_company_facts", return_value=json.loads(
                (Path(__file__).parent / "fixtures" / "edgar_aapl_companyfacts_sample.json").read_text(encoding="utf-8"))),
        ]
        self.seen: list[tuple[str, str]] = []

        def build(cfg):
            def adapter(subject, context, directive):
                self.seen.append((context, directive.version))
                block = json.dumps({**GOOD, "direction": "buy" if "bull analyst" in context else "sell"})
                return _parse_response(three(f"<assessment>{block}</assessment>"),
                                       allow_assessment=expects_assessment(directive.version))
            return adapter
        self._patches.append(patch("web.server._build_adapter", side_effect=build))
        from tests.support import no_model_extraction
        self._patches.append(no_model_extraction())
        # v1.6.0 isn't the active directive (reverted 2026-09-26); these tests put it in
        # place to exercise the assessment path end to end.
        self._patches.append(patch("core.directive.ACTIVE_DIRECTIVE", DIRECTIVE_V1_6_0))
        for p in self._patches:
            p.start()
        from web.server import app
        self.client = TestClient(app)

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_same_figures_opposite_briefs_and_each_sides_assessment(self):
        tok = self.client.post("/api/auth/token", json={"scope": "auditor"}).json()["access_token"]
        d = self.client.post("/api/compare", json={"ticker": "AAPL", "pairing": "bull_bear"},
                             headers={"Authorization": f"Bearer {tok}"}).json()
        self.assertEqual((d["producers"]["a"]["role"], d["producers"]["b"]["role"]), ("BULL", "BEAR"))
        self.assertEqual(d["producers"]["pairing"], "bull_bear")
        self.assertEqual(d["cross_agent_comparison"]["contradiction_rule"], "canonical_facts")
        strip = lambda c: "\n".join(line for line in c.splitlines() if not line.startswith("Brief:"))
        self.assertEqual(strip(d["contexts"]["a"]), strip(d["contexts"]["b"]))
        self.assertIn("bull analyst", d["contexts"]["a"])
        self.assertIn("bear analyst", d["contexts"]["b"])
        by_agent = {o["agent_id"]: o for o in d["reasoning_objects"]}
        self.assertEqual(by_agent["bull"]["assessment"]["direction"], "buy")
        self.assertEqual(by_agent["bear"]["assessment"]["direction"], "sell")
        self.assertEqual({v for _, v in self.seen}, {"v1.6.0"})
        self.assertEqual({s.get("slot") for s in d["steps"] if s["label"].startswith("summarize")}, {"a", "b"})

    def test_under_the_active_directive_no_assessment_is_asked_for(self):
        with patch("core.directive.ACTIVE_DIRECTIVE", DIRECTIVE_V1_5_2):
            tok = self.client.post("/api/auth/token", json={"scope": "auditor"}).json()["access_token"]
            d = self.client.post("/api/compare", json={"ticker": "AAPL", "pairing": "bull_bear"},
                                 headers={"Authorization": f"Bearer {tok}"}).json()
        # The scripted agents still append a block; under v1.5.2 that is text outside the
        # two blocks, so the old contract rejects it — exactly as before B4.
        self.assertEqual(d["cross_agent_comparison"]["status"], "BOTH_HALTED")
        self.assertTrue(all(o.get("assessment_status") is None for o in d["reasoning_objects"]))

    def test_the_original_pairing_is_unchanged(self):
        tok = self.client.post("/api/auth/token", json={"scope": "auditor"}).json()["access_token"]
        d = self.client.post("/api/compare", json={"ticker": "AAPL"}, headers={"Authorization": f"Bearer {tok}"}).json()
        self.assertEqual((d["producers"]["a"]["role"], d["producers"]["pairing"]), ("PRODUCER A", "lenses"))
        self.assertEqual(d["cross_agent_comparison"]["contradiction_rule"], "concept_aware")
        self.assertEqual([s["label"] for s in d["steps"] if s["label"].startswith("summarize")],
                         ["summarize_facts", "summarize_earnings_facts"])


if __name__ == "__main__":
    unittest.main()
