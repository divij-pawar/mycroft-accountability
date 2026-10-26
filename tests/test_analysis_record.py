"""
The analysis record (core/analysis.py) and its store (web/db.py's analysis tables).

What this pins
    - The signal vocabulary: five levels in rank order, letters A-E with A the strongest buy
      (PLAN.md 6.2, decided 2026-10-06), and distance as a subtraction of ranks.
    - validate_signal never coerces: "Buy", "A" or "strong buy" is not a signal. A bad key point
      or figure is dropped on its own, with the reason, and the rest is kept.
    - Hashing is canonical: key order doesn't change it, and NaN is refused.
    - The store is append-only (updates and deletes refused by SQLite itself), a bundle is
      stored once per hash, a run can't reference a bundle that isn't stored, a verdict outside
      the vocabulary is refused, and the TTL purge puts its guards back.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import analysis as A


class TestVocabulary(unittest.TestCase):
    def test_letters_and_ranks(self):
        self.assertEqual([A.signal_letter(s) for s in reversed(A.SIGNALS)], list("ABCDE"))
        self.assertEqual(A.signal_label("buy"), "B · Buy")
        self.assertEqual(A.signal_distance("buy", "buy"), 0)
        self.assertEqual(A.signal_distance("buy", "hold"), 1)
        self.assertEqual(A.signal_distance("strong_sell", "strong_buy"), 4)
        self.assertEqual(A.DEFAULT_HORIZON, "3-6m")
        self.assertEqual(set(A.HORIZON_WORDS), set(A.HORIZONS))
        with self.assertRaises(ValueError):
            A.signal_rank("A")

    def test_verdicts_match_the_store(self):
        from web import db
        self.assertIn("('CONSISTENT','ADJACENT','DIVERGENT','UNGROUNDED','INCONCLUSIVE')", db._SCHEMA_SQL)
        self.assertEqual(set(A.VERDICTS), {"CONSISTENT", "ADJACENT", "DIVERGENT", "UNGROUNDED", "INCONCLUSIVE"})
        self.assertIn("('builtin','session','byo')", db._SCHEMA_SQL)
        self.assertEqual(A.AGENT_KINDS, ("builtin", "session", "byo"))
        self.assertIn("('ok','halted','abstained')", db._SCHEMA_SQL)
        self.assertEqual(A.RUN_STATUSES, ("ok", "halted", "abstained"))


GOOD = {
    "signal": "buy", "horizon": "3-6m", "conviction": "medium",
    "key_points": [{"text": "RSI is neutral and price is above its 50-day average.", "evidence": [{"call": 2}]}],
    "cited_figures": [{"name": "rsi_14", "value": 53.78, "unit": "index", "ref": {"call": 2}},
                      {"name": "net_income", "value": 29.79e9, "ref": {"call": 3}}],
}


class TestValidateSignal(unittest.TestCase):
    def test_a_good_signal_is_kept_whole(self):
        p = A.validate_signal(GOOD)
        self.assertEqual((p.status, p.issues), ("valid", []))
        self.assertEqual(p.signal["signal"], "buy")
        self.assertEqual(len(p.signal["cited_figures"]), 2)

    def test_signals_outside_the_vocabulary_are_never_coerced(self):
        for bad in ("Buy", "A", "strong buy", "STRONG_BUY", "", None, 4):
            with self.subTest(bad=bad):
                p = A.validate_signal({**GOOD, "signal": bad})
                self.assertEqual((p.status, p.signal), ("invalid", None))

    def test_bad_optional_fields_are_dropped_with_reasons(self):
        p = A.validate_signal({**GOOD, "horizon": "3-6 months", "conviction": "very high", "grade": "AA"})
        self.assertEqual(p.status, "partial")
        self.assertNotIn("horizon", p.signal)
        self.assertNotIn("conviction", p.signal)
        self.assertEqual(len(p.issues), 3)
        self.assertIn("unknown field 'grade' dropped", p.issues)

    def test_bad_figures_are_dropped_one_by_one(self):
        figures = [
            {"name": "ok", "value": 1.5, "ref": {"call": 1}},
            {"name": "text", "value": "1.5", "ref": {"call": 1}},
            {"name": "nan", "value": float("nan"), "ref": {"call": 1}},
            {"name": "bool", "value": True, "ref": {"call": 1}},
            {"name": "noref", "value": 2},
            {"name": "tworefs", "value": 2, "ref": {"call": 1, "chunk": "x"}},
            {"name": "seq0", "value": 2, "ref": {"call": 0}},
            {"name": "judged", "value": 2, "ref": {"judgment": True}},
            {"name": "doc", "value": 2, "ref": {"chunk": "sha256:ab:3"}},
        ]
        p = A.validate_signal({**GOOD, "cited_figures": figures})
        self.assertEqual([f["name"] for f in p.signal["cited_figures"]], ["ok", "judged", "doc"])
        self.assertEqual(len(p.issues), 6)

    def test_key_points_are_capped_and_checked(self):
        points = [{"text": f"point {i}", "evidence": []} for i in range(5)]
        p = A.validate_signal({**GOOD, "key_points": points})
        self.assertEqual(len(p.signal["key_points"]), A.MAX_KEY_POINTS)
        p = A.validate_signal({**GOOD, "key_points": [{"text": "  "}, {"text": "x", "evidence": [{"web": "u"}]}]})
        self.assertEqual(p.signal["key_points"], [])
        self.assertEqual(len(p.issues), 2)

    def test_not_an_object(self):
        self.assertEqual(A.validate_signal(["buy"]).status, "invalid")


class TestHashing(unittest.TestCase):
    def test_key_order_does_not_matter(self):
        self.assertEqual(A.content_hash({"a": 1, "b": [1, 2]}), A.content_hash({"b": [1, 2], "a": 1}))
        self.assertNotEqual(A.content_hash([1, 2]), A.content_hash([2, 1]))

    def test_nan_and_infinity_are_refused(self):
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                A.content_hash({"x": bad})


def _bundle(h="sha256:" + "a" * 64):
    return {"bundle_hash": h, "created_at": "2026-10-06T01:00:00Z",
            "data": {"schema": A.BUNDLE_SCHEMA, "ticker": "AAPL", "as_of": "2026-10-05"}}


def _record(run_id="r1", h="sha256:" + "a" * 64, **kw):
    return {"schema": A.RECORD_SCHEMA, "run_id": run_id, "created_at": "2026-10-06T01:00:00.000000Z",
            "request": {"message": "Should I buy AAPL?", "ticker": "AAPL", "horizon": "3-6m"},
            "agent": {"kind": "builtin", "model": "llama3.2", "reasoning": "none"},
            "bundle_hash": h, "inputs_captured": True, "tool_calls": [], "status": "ok",
            "output": {"signal": "buy"}, **kw}


class TestStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._patch = patch("web.db.DB_PATH", Path(self._tmp) / "t.db")
        self._patch.start()
        from web import db
        self.db = db
        db.init_db()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)   # SQLite's WAL files can stay locked on Windows

    def test_round_trip(self):
        self.assertTrue(self.db.insert_bundle(_bundle()))
        self.assertFalse(self.db.insert_bundle(_bundle()))           # the same data, stored once
        self.db.insert_analysis(_record())
        self.assertEqual(self.db.get_bundle(_bundle()["bundle_hash"]), _bundle())
        self.assertEqual(self.db.get_analysis("r1"), _record())
        listed = self.db.list_analyses()
        self.assertEqual([r["run_id"] for r in listed], ["r1"])
        self.assertNotIn("tool_calls", listed[0])
        self.assertEqual(self.db.list_analyses(ticker="msft"), [])

    def test_a_run_needs_its_bundle(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.insert_analysis(_record(h="sha256:" + "b" * 64))

    def test_vocabulary_is_enforced_by_sqlite(self):
        self.db.insert_bundle(_bundle())
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.insert_analysis(_record(status="done"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.insert_analysis(_record(agent={"kind": "robot"}))
        self.db.insert_analysis(_record())
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.insert_verdict({"run_id": "r1", "verdict": "GREEN"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.insert_bundle({**_bundle("md5:abc")})

    def test_verdicts_append_in_order(self):
        self.db.insert_bundle(_bundle())
        self.db.insert_analysis(_record())
        self.db.insert_verdict({"run_id": "r1", "verdict": "DIVERGENT"})
        v2 = self.db.insert_verdict({"run_id": "r1", "verdict": "CONSISTENT"})
        self.assertEqual([v["verdict"] for v in self.db.get_verdicts("r1")], ["DIVERGENT", "CONSISTENT"])
        self.assertTrue(v2["verdict_id"])

    def test_append_only(self):
        self.db.insert_bundle(_bundle())
        self.db.insert_analysis(_record())
        self.db.insert_verdict({"run_id": "r1", "verdict": "ADJACENT"})
        with self.db._connect() as c:
            for table in ("analysis_bundles", "analysis_runs", "analysis_verdicts"):
                with self.subTest(table=table):
                    with self.assertRaises(sqlite3.IntegrityError):
                        c.execute(f"UPDATE {table} SET payload_json = '{{}}'")
                    with self.assertRaises(sqlite3.IntegrityError):
                        c.execute(f"DELETE FROM {table}")

    def test_ttl_purge_removes_runs_verdicts_and_orphan_bundles_then_restores_guards(self):
        self.db.insert_bundle(_bundle())
        self.db.insert_analysis(_record())
        self.db.insert_verdict({"run_id": "r1", "verdict": "ADJACENT"})
        self.assertEqual(self.db.purge_old_analyses(retention_days=-1), 1)
        self.assertIsNone(self.db.get_analysis("r1"))
        self.assertEqual(self.db.get_verdicts("r1"), [])
        self.assertIsNone(self.db.get_bundle(_bundle()["bundle_hash"]))
        self.db.insert_bundle(_bundle())
        with self.db._connect() as c, self.assertRaises(sqlite3.IntegrityError):
            c.execute("DELETE FROM analysis_bundles")

    def test_purge_keeps_recent_rows(self):
        self.db.insert_bundle(_bundle())
        self.db.insert_analysis(_record(created_at=None))
        self.assertEqual(self.db.purge_old_analyses(), 0)
        self.assertIsNotNone(self.db.get_analysis("r1"))

    def test_clear_all_drops_the_analysis_tables_too(self):
        self.db.insert_bundle(_bundle())
        self.db.insert_analysis(_record())
        self.db.clear_all()
        self.assertEqual(self.db.list_analyses(), [])
        self.assertIsNone(self.db.get_bundle(_bundle()["bundle_hash"]))

    def test_stored_json_round_trips_exactly(self):
        b = _bundle()
        b["data"]["rows"] = [["2026-10-05", 332.815, 336.21, 0.1 + 0.2, 1e-7, 34400850]]
        b["bundle_hash"] = A.content_hash(b["data"])
        self.db.insert_bundle(b)
        self.assertEqual(A.content_hash(self.db.get_bundle(b["bundle_hash"])["data"]), b["bundle_hash"])
        self.assertEqual(json.dumps(self.db.get_bundle(b["bundle_hash"])), json.dumps(b))


if __name__ == "__main__":
    unittest.main()
