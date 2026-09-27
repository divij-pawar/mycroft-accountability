"""
BP — locating a companyfacts figure in its filing's inline XBRL (datasources/filings.py),
the /api/facts/excerpt and /api/runs/{id}/source-snippet routes, and the per-result
search snippets the LangChain adapter now records.

The filing fixture is real: Apple's 10-Q for the quarter ended 2026-06-27 (accession
0000320193-26-000020), trimmed to its xbrli:contexts and two statement tables, kept
verbatim (tests/fixtures/aapl_10q_2026q3_trimmed.htm). The expected values are the
ones companyfacts gave the agents in the live AAPL run of 2026-09-25 (logs/RUN_LOG.md).

No network: the fetcher is injected.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from adapters.langchain_adapter import _result_snippets
from datasources import filings as F

_DOC = (Path(__file__).parent / "fixtures" / "aapl_10q_2026q3_trimmed.htm").read_text(encoding="utf-8")
CIK, ACCN = "0000320193", "0000320193-26-000020"
Q3 = ("2026-03-29", "2026-06-27")


def _submissions(inline=1, accn=ACCN):
    return json.dumps({"filings": {"recent": {
        "accessionNumber": [accn], "primaryDocument": ["aapl-20260627.htm"], "form": ["10-Q"],
        "filingDate": ["2026-07-31"], "reportDate": ["2026-06-27"], "isInlineXBRL": [inline],
    }}}).encode()


class FakeSec:
    def __init__(self, doc=_DOC, inline=1, accn=ACCN, doc_error=None):
        self.calls: list[str] = []
        self.doc, self.inline, self.accn, self.doc_error = doc, inline, accn, doc_error

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if "submissions" in url:
            return _submissions(self.inline, self.accn)
        if self.doc_error:
            raise self.doc_error
        return self.doc.encode()


class TestFindInDocument(unittest.TestCase):

    def _one(self, concept, start, end):
        occ, tag = F.find_in_document(_DOC, concept, start, end)
        self.assertEqual(len(occ), 1, f"{concept}: {occ}")
        return occ[0], tag

    def test_quarterly_revenue_is_the_total_net_sales_row(self):
        o, tag = self._one("Revenues", *Q3)
        # companyfacts' Revenues fell through to the ASC 606 tag (datasources.edgar.CONCEPT_TAGS).
        self.assertEqual(tag, "RevenueFromContractWithCustomerExcludingAssessedTax")
        self.assertEqual((o.row_label, o.displayed, o.value, o.scale), ("Total net sales", "109,417", 109417e6, 6))
        self.assertIn("Three Months Ended", o.column_header)
        self.assertIn("June 27", o.column_header)
        self.assertEqual(o.excerpt[o.highlight[0]:o.highlight[1]], "109,417")
        self.assertTrue(o.in_table)

    def test_the_other_given_figures(self):
        self.assertEqual(self._one("NetIncomeLoss", *Q3)[0].value, 29789e6)
        eps, _ = self._one("EarningsPerShareDiluted", *Q3)
        self.assertEqual((eps.row_label, eps.value), ("Diluted", 2.02))
        assets, _ = self._one("Assets", None, "2026-06-27")
        self.assertEqual((assets.row_label, assets.value), ("Total assets", 383266e6))
        self.assertNotIn("ASSETS", assets.column_header)  # a section title is not a column header

    def test_the_highlight_is_the_values_own_cell_not_an_equal_number_elsewhere(self):
        o, _ = self._one("NetIncomeLoss", *Q3)
        before = o.excerpt[:o.highlight[0]]
        self.assertTrue(before.startswith("Net income"))
        self.assertNotIn("29,789", before)

    def test_other_periods_and_breakdowns_are_not_the_consolidated_figure(self):
        # The nine-month figure is a different context; so is the Products/Services split.
        nine, _ = F.find_in_document(_DOC, "Revenues", "2025-09-28", "2026-06-27")
        self.assertEqual([o.value for o in nine], [364357e6])
        self.assertEqual(F.find_in_document(_DOC, "Revenues", "2026-01-01", "2026-06-27")[0], [])

    def test_sign_scale_and_prose(self):
        html = ('<ix:header><xbrli:context id="c1"><xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate>'
                '<xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period></xbrli:context></ix:header>'
                '<p>The company recorded a net loss of $<ix:nonFraction name="us-gaap:NetIncomeLoss" contextRef="c1" '
                'scale="3" sign="-">1,250</ix:nonFraction> thousand for the quarter.</p>')
        occ, _ = F.find_in_document(html, "NetIncomeLoss", "2026-01-01", "2026-03-31")
        self.assertEqual(occ[0].value, -1250000)
        self.assertFalse(occ[0].in_table)
        self.assertIn("net loss of $1,250 thousand", occ[0].excerpt)
        self.assertEqual(occ[0].excerpt[occ[0].highlight[0]:occ[0].highlight[1]], "1,250")


class TestFindExcerpt(unittest.TestCase):

    def setUp(self):
        self.cache = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.cache, ignore_errors=True)

    def test_found_and_cached(self):
        sec = FakeSec()
        ex = F.find_excerpt(CIK, ACCN, "Revenues", *Q3, fetch=sec, cache_dir=self.cache).to_dict()
        self.assertEqual(ex["status"], "found")
        self.assertEqual((ex["form"], ex["filed"], ex["scale_words"]), ("10-Q", "2026-07-31", "millions"))
        self.assertEqual(ex["best"]["row_label"], "Total net sales")
        self.assertEqual(ex["document_url"],
                         "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627.htm")
        self.assertTrue(ex["filing_url"].endswith("/0000320193-26-000020-index.htm"))
        F.find_excerpt(CIK, ACCN, "NetIncomeLoss", *Q3, fetch=sec, cache_dir=self.cache)
        self.assertEqual(sum("aapl-20260627.htm" in u for u in sec.calls), 1, "the document is fetched once, then cached")

    def test_every_odd_case_says_why_and_still_links_the_filing(self):
        cases = [
            (FakeSec(inline=0), "no_inline_xbrl"),
            (FakeSec(accn="0000320193-25-000001"), "not_in_index"),
            (FakeSec(doc_error=F.FilingFetchError("x: larger than 25000000 bytes")), "too_large"),
            (FakeSec(doc_error=F.FilingFetchError("x: timed out")), "fetch_failed"),
            (FakeSec(), "not_found"),
        ]
        for i, (sec, status) in enumerate(cases):
            concept = "PaymentsToAcquirePropertyPlantAndEquipment" if status == "not_found" else "Revenues"
            ex = F.find_excerpt(CIK, ACCN, concept, *Q3, fetch=sec, cache_dir=self.cache / str(i)).to_dict()
            self.assertEqual(ex["status"], status)
            self.assertTrue(ex["filing_url"].startswith("https://www.sec.gov/Archives/edgar/data/320193/"))
            self.assertTrue(ex["message"])


class TestSnippets(unittest.TestCase):

    def test_adapter_records_what_the_agent_read_per_result(self):
        got = _result_snippets({"results": [
            {"url": "https://a.example", "title": "A", "content": "x" * 900},
            {"url": "https://b.example"}, {"title": "no url"},
        ]})
        self.assertEqual([g["url"] for g in got], ["https://a.example", "https://b.example"])
        self.assertEqual(len(got[0]["snippet"]), 600)
        self.assertIsNone(got[1]["snippet"])
        self.assertEqual(_result_snippets("Error: rate limited"), [])


class TestRoutes(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db = patch("web.db.DB_PATH", Path(self._tmpdir) / "t.db")
        self._db.start()
        from web import db
        db.init_db()
        self.db = db
        cache = Path(self._tmpdir) / "cache"
        real = F.find_excerpt
        self._fx = patch("web.server.find_excerpt",
                         side_effect=lambda *a, **k: real(*a, fetch=FakeSec(), cache_dir=cache, **k))
        self._fx.start()
        from web.server import app
        self.client = TestClient(app)

    def tearDown(self):
        self._fx.stop()
        self._db.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_excerpt_route(self):
        r = self.client.get("/api/facts/excerpt", params={"cik": CIK, "accn": ACCN, "concept": "Assets", "end": "2026-06-27"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["best"]["row_label"], "Total assets")

    def test_malformed_parameters_never_reach_a_url(self):
        for bad in ({"accn": "../../etc"}, {"cik": "12a"}, {"concept": "Revenues/../x"}, {"end": "2026-6-27"}):
            params = {"cik": CIK, "accn": ACCN, "concept": "Revenues", "end": "2026-06-27", **bad}
            self.assertEqual(self.client.get("/api/facts/excerpt", params=params).status_code, 422, bad)

    def test_source_snippet_route(self):
        self.db.insert_run({
            "run_id": "r1", "subject": "AAPL", "scope": "auditor",
            "session": {"initiated_at": "2026-09-25T00:00:00+00:00"},
            "steps": [{"phase": "agent_a", "kind": "tool", "query": "AAPL Q3 revenue", "results": [
                {"url": "https://news.example/aapl", "title": "Apple Q3",
                 "snippet": "Apple reported revenue of 94.9 billion dollars for the quarter."}]}],
        })
        self.db.insert_run({"run_id": "r0", "subject": "AAPL", "scope": "auditor",
                            "session": {"initiated_at": "2026-09-24T00:00:00+00:00"}, "steps": [{"phase": "agent_a"}]})
        got = self.client.get("/api/runs/r1/source-snippet",
                              params={"url": "https://news.example/aapl", "figure": "$94.9 billion"}).json()
        self.assertEqual((got["status"], got["agent"], got["query"]), ("found", "a", "AAPL Q3 revenue"))
        self.assertEqual(got["snippet"][got["highlight"][0]:got["highlight"][1]], "94.9")
        missing = self.client.get("/api/runs/r1/source-snippet", params={"url": "https://other.example"}).json()
        self.assertEqual(missing["status"], "not_found")
        old = self.client.get("/api/runs/r0/source-snippet", params={"url": "https://news.example/aapl"}).json()
        self.assertIn("recorded before search snippets were kept", old["message"])


if __name__ == "__main__":
    unittest.main()
