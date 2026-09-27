"""
BP — find a reported figure in the filing itself (inline XBRL), so a reviewer can
see the table row the number actually sits in.

Why this exists
    Every figure the agents are given comes from SEC's companyfacts API
    (datasources/edgar.py), which is numbers only: no text, no table. "Where in the
    10-Q is this?" needs the filing document. Since 2019 the primary 10-Q/10-K
    document is inline XBRL: ordinary HTML whose figures are wrapped in
    <ix:nonFraction name="us-gaap:Revenues" contextRef="c-10" scale="6">94,930</…>,
    with each contextRef defined once (period, and any dimension) in an
    <xbrli:context>. So a companyfacts Fact (concept, period, accession number) can
    be located exactly: same tag, a context with the same period and no dimension.

What it returns (Excerpt)
    The row label and column header of the table cell holding the value, the row's
    text with the value's position marked, the value as filed ("94,930") and what it
    means once scale and sign are applied, the filing's URL, and how many times the
    same fact appears (statements, notes, MD&A all repeat it). A value outside any
    table gets its enclosing paragraph instead. For financial figures the honest form
    of "the exact paragraph" is usually a table row; the result says which it is.

Network and cost (P2)
    Only this module fetches filings, and only when a reviewer asks (the route is
    lazy). Two SEC requests per filing — the submissions index (to find the primary
    document) and the document — rate-limited per process under SEC's 10 requests/s
    fair-access limit, with the same configurable SEC_USER_AGENT as edgar.py. Fetched
    documents are cached gzipped under web/data/filing_cache/ (gitignored,
    regenerable). A document over MAX_BYTES is refused with a reason, not parsed.

Parsing is stdlib html.parser, streaming, no new dependency.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Literal

from datasources.edgar import CONCEPT_TAGS, _DEFAULT_USER_AGENT

MAX_BYTES = 25_000_000          # a large 10-K's primary document runs to ~10MB
_TIMEOUT_S = 20
_MIN_INTERVAL_S = 0.12          # ≤ ~8 requests/s, under SEC's 10/s
CACHE_DIR = Path(__file__).resolve().parent.parent / "web" / "data" / "filing_cache"

Status = Literal["found", "not_found", "no_inline_xbrl", "too_large", "fetch_failed", "not_in_index"]

_SCALE_WORDS = {3: "thousands", 6: "millions", 9: "billions"}


# ── Fetching ───────────────────────────────────────────────────────────────────

class FilingFetchError(Exception):
    pass


_rate_lock = threading.Lock()
_last_request = [0.0]


def _get(url: str, *, limit: int = MAX_BYTES) -> bytes:
    """GET with the SEC User-Agent, the per-process rate limit and a byte ceiling."""
    with _rate_lock:
        wait = _last_request[0] + _MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()
    ua = os.environ.get("SEC_USER_AGENT") or _DEFAULT_USER_AGENT
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            body = resp.read(limit + 1)
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FilingFetchError(f"{url}: {exc}") from exc
    if len(body) > limit:
        raise FilingFetchError(f"{url}: larger than {limit} bytes")
    return body


Fetcher = Callable[[str], bytes]


def accn_folder(cik: str, accn: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}"


def filing_index_url(cik: str, accn: str) -> str:
    """The filing's human-readable index page on EDGAR (always exists)."""
    return f"{accn_folder(cik, accn)}/{accn}-index.htm"


def primary_document(cik: str, accn: str, fetch: Fetcher = _get, cache_dir: Path | None = None) -> dict[str, Any] | None:
    """
    The submissions-index entry for `accn`: primary document name, form, dates,
    inline-XBRL flag. A filing's entry never changes once filed, so it is cached
    beside the document; only the first lookup per filing reads the (large) index.
    """
    cache = (cache_dir or CACHE_DIR) / f"{accn}.meta.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    meta = _primary_document_uncached(cik, accn, fetch)
    if meta is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(meta), encoding="utf-8")
    return meta


def _primary_document_uncached(cik: str, accn: str, fetch: Fetcher) -> dict[str, Any] | None:
    data = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"))
    recent = (data.get("filings") or {}).get("recent") or {}
    accns = recent.get("accessionNumber") or []
    if accn not in accns:
        return None  # older than the ~1000 most recent filings; the index pages carry the rest
    i = accns.index(accn)
    pick = lambda k: (recent.get(k) or [None] * len(accns))[i]  # noqa: E731
    return {
        "primary_document": pick("primaryDocument"),
        "form": pick("form"),
        "filed": pick("filingDate"),
        "report_date": pick("reportDate"),
        "inline_xbrl": bool(pick("isInlineXBRL")),
    }


def load_document(cik: str, accn: str, name: str, fetch: Fetcher = _get, cache_dir: Path | None = None) -> str:
    """The primary document's text, from the gzip cache or SEC."""
    cache_dir = cache_dir or CACHE_DIR
    path = cache_dir / f"{accn}.htm.gz"
    if path.exists():
        return gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace")
    body = fetch(f"{accn_folder(cik, accn)}/{name}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body))
    return body.decode("utf-8", errors="replace")


# ── Parsing ────────────────────────────────────────────────────────────────────

@dataclass
class _Cell:
    text: list[str] = field(default_factory=list)
    col: int = 0
    span: int = 1


@dataclass
class Occurrence:
    fact_id: str | None
    context: str
    displayed: str                 # the characters in the filing, e.g. "94,930"
    value: float | None            # displayed × 10^scale, sign applied
    scale: int
    in_table: bool
    row_label: str | None
    column_header: str | None
    excerpt: str
    highlight: tuple[int, int] | None   # [start, end) of `displayed` within `excerpt`


_FIGURE_CELL = re.compile(r"[\s$€£(]*-?[\d,]*\d(?:\.\d+)?\)?%?\s*")
_YEAR_CELL = re.compile(r"\s*(?:19|20)\d{2}\s*")


def _is_figure_cell(text: str) -> bool:
    """A cell that is only a number — not a year heading, not a date like "March 31,"."""
    return bool(_FIGURE_CELL.fullmatch(text)) and not _YEAR_CELL.fullmatch(text)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _number(displayed: str, fmt: str | None) -> float | None:
    s = displayed.strip()
    if not s or s in ("—", "-", "–"):
        return 0.0 if s else None
    if fmt and "zerodash" in fmt:
        return 0.0
    if fmt and "comma-decimal" in fmt:     # European: 1.234,5
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    s = re.sub(r"[^\d.]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


# html.parser lowercases tag names; the period elements map onto a context's fields.
_PERIOD_FIELDS = {"xbrli:startdate": "start", "xbrli:enddate": "end", "xbrli:instant": "instant"}


class _IxParser(HTMLParser):
    """
    One pass over an inline-XBRL document: every context's period and whether it
    has a dimension, and every ix:nonFraction for the wanted tags with its table
    cell (or paragraph) around it. Table structure is tracked with colspan so a
    column header can be matched to the value's column.
    """

    def __init__(self, tags: set[str]):
        super().__init__(convert_charrefs=True)
        self.tags = tags
        self.contexts: dict[str, dict[str, Any]] = {}
        self._ctx: dict[str, Any] | None = None
        self._ctx_field: str | None = None
        self.raw: list[dict[str, Any]] = []
        # table state (innermost table only; filings don't nest financial tables)
        self._rows: list[list[_Cell]] | None = None
        self._row: list[_Cell] | None = None
        self._cell: _Cell | None = None
        self._next_col = 0
        self._table_facts: list[dict[str, Any]] = []
        # paragraph fallback
        self._para: list[str] | None = None
        self._para_facts: list[dict[str, Any]] = []
        # the fact being read
        self._fact: dict[str, Any] | None = None
        self._hidden_depth = 0

    # contexts ------------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "xbrli:context":
            self._ctx = {"id": a.get("id"), "start": None, "end": None, "instant": None, "dimensional": False}
        elif self._ctx is not None:
            if tag in _PERIOD_FIELDS:
                self._ctx_field = _PERIOD_FIELDS[tag]
            elif tag in ("xbrli:segment", "xbrli:scenario"):
                self._ctx["dimensional"] = True
        elif tag == "ix:header":
            self._hidden_depth += 1
        elif tag == "table":
            self._rows, self._table_facts = [], []
        elif tag == "tr" and self._rows is not None:
            self._row, self._next_col = [], 0
        elif tag in ("td", "th") and self._row is not None:
            span = int(a.get("colspan") or 1) if (a.get("colspan") or "1").isdigit() else 1
            self._cell = _Cell(col=self._next_col, span=span)
            self._next_col += span
            self._row.append(self._cell)
        elif tag in ("p", "div") and self._rows is None and self._para is None:
            self._para, self._para_facts = [], []
        elif tag == "br":
            self.handle_data(" ")  # "As of<br>June 30" reads "As of June 30"
        elif tag == "ix:nonfraction" and a.get("name", "").split(":")[-1] in self.tags:
            self._fact = {
                "id": a.get("id"), "tag": a.get("name", "").split(":")[-1], "context": a.get("contextref"),
                "scale": int(a.get("scale") or 0), "sign": a.get("sign"), "format": a.get("format"),
                "text": [], "cell": self._cell, "row": self._row,
            }

    def handle_endtag(self, tag: str) -> None:
        if tag == "xbrli:context" and self._ctx is not None:
            self.contexts[self._ctx["id"]] = self._ctx
            self._ctx = None
        elif tag in _PERIOD_FIELDS:
            self._ctx_field = None
        elif tag == "ix:header":
            self._hidden_depth = max(0, self._hidden_depth - 1)
        elif tag == "ix:nonfraction" and self._fact is not None:
            f, self._fact = self._fact, None
            f["displayed"] = _clean("".join(f["text"]))
            if f["cell"] is not None:
                self._table_facts.append(f)
            elif self._para is not None:
                f["para_offset"] = len("".join(self._para)) - len("".join(f["text"]))
                self._para_facts.append(f)
            else:
                self._finish(f, in_table=False, excerpt=f["displayed"])
        elif tag in ("td", "th"):
            self._cell = None
        elif tag == "tr" and self._rows is not None and self._row is not None:
            self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._rows is not None:
            rows = self._rows
            for f in self._table_facts:
                self._finish_table(f, rows)
            self._rows, self._table_facts = None, []
        elif tag in ("p", "div") and self._para is not None and self._rows is None:
            text = "".join(self._para)
            for f in self._para_facts:
                self._finish_para(f, text)
            self._para, self._para_facts = None, []

    def handle_data(self, data: str) -> None:
        if self._ctx is not None and self._ctx_field:
            self._ctx[self._ctx_field] = data.strip()
            return
        if self._fact is not None:
            self._fact["text"].append(data)
        if self._cell is not None:
            self._cell.text.append(data)
        elif self._para is not None:
            self._para.append(data)

    # results -------------------------------------------------------------------
    def _finish(self, f: dict[str, Any], *, in_table: bool, excerpt: str, row_label: str | None = None,
                column_header: str | None = None, highlight: tuple[int, int] | None = None) -> None:
        v = _number(f["displayed"], f["format"])
        if v is not None:
            v = v * 10 ** f["scale"]
            if f["sign"] == "-":
                v = -v
        self.raw.append({
            "tag": f["tag"],
            "occ": Occurrence(f["id"], f["context"], f["displayed"], v, f["scale"], in_table,
                              row_label, column_header, excerpt, highlight),
        })

    def _finish_table(self, f: dict[str, Any], rows: list[list[_Cell]]) -> None:
        row, cell = f["row"], f["cell"]
        texts = [_clean("".join(c.text)) for c in row]
        label = next((t for t in texts if t and not re.fullmatch(r"[\d,.$()%—–\-\s]+", t)), None)
        # Header: the text above this column, from the rows before the first row with a figure.
        header_rows: list[list[_Cell]] = []
        for r in rows:
            if r is row or any(_is_figure_cell(_clean("".join(c.text))) for c in r):
                break
            header_rows.append(r)
        parts = []
        for r in header_rows:
            for c in r:
                if c.col == 0:
                    continue  # the row-label column ("ASSETS:", a section title), never a column header
                if c.col <= cell.col < c.col + c.span or (c.col <= cell.col + cell.span - 1 < c.col + c.span):
                    t = _clean("".join(c.text))
                    if t and t not in parts:
                        parts.append(t)
        excerpt = " | ".join(t for t in texts if t)
        shown = f["displayed"]
        # Mark the value's own cell, not an equal number elsewhere in the row.
        before = " | ".join(t for c, t in zip(row, texts) if t and c.col < cell.col)
        start = excerpt.find(shown, len(before))
        self._finish(f, in_table=True, excerpt=excerpt, row_label=label,
                     column_header=" · ".join(parts) or None,
                     highlight=(start, start + len(shown)) if start >= 0 else None)

    def _finish_para(self, f: dict[str, Any], text: str) -> None:
        clean = _clean(text)
        shown = f["displayed"]
        at = clean.find(shown)
        lo = max(0, at - 240) if at >= 0 else 0
        excerpt = clean[lo: (at + len(shown) + 240) if at >= 0 else 480]
        start = excerpt.find(shown)
        self._finish(f, in_table=False, excerpt=excerpt,
                     highlight=(start, start + len(shown)) if start >= 0 else None)


# ── Entry point ────────────────────────────────────────────────────────────────

@dataclass
class Excerpt:
    status: Status
    filing_url: str
    document_url: str | None = None
    form: str | None = None
    filed: str | None = None
    concept: str | None = None
    tag: str | None = None
    period: str | None = None
    occurrences: list[Occurrence] = field(default_factory=list)
    occurrence_count: int = 0
    scale_words: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["best"] = d["occurrences"][0] if d["occurrences"] else None
        return d


def _period_matches(ctx: dict[str, Any], start: str | None, end: str) -> bool:
    if ctx.get("dimensional"):
        return False  # a segment/axis breakdown, not the consolidated figure
    if start:
        return ctx.get("start") == start and ctx.get("end") == end
    return ctx.get("instant") == end


def find_in_document(html: str, concept: str, start: str | None, end: str) -> tuple[list[Occurrence], str | None]:
    """Every consolidated occurrence of `concept` for the period, best first; and the tag matched."""
    tags = set(CONCEPT_TAGS.get(concept, (concept,)))
    parser = _IxParser(tags)
    parser.feed(html)
    parser.close()
    hits = [(r["tag"], r["occ"]) for r in parser.raw
            if (ctx := parser.contexts.get(r["occ"].context)) and _period_matches(ctx, start, end)]
    # Prefer a table row (a statement) over prose, then document order.
    hits.sort(key=lambda h: not h[1].in_table)
    return [o for _, o in hits], (hits[0][0] if hits else None)


def find_excerpt(
    cik: str,
    accn: str,
    concept: str,
    start: str | None,
    end: str,
    *,
    fetch: Fetcher = _get,
    cache_dir: Path | None = None,
    max_occurrences: int = 5,
) -> Excerpt:
    """Locate a companyfacts Fact in its filing. Never raises for a missing or odd filing — says why."""
    index = filing_index_url(cik, accn)
    period = f"{start} to {end}" if start else f"as of {end}"
    base = Excerpt("not_found", index, concept=concept, period=period)
    try:
        meta = primary_document(cik, accn, fetch, cache_dir)
    except (FilingFetchError, ValueError) as exc:
        return Excerpt("fetch_failed", index, concept=concept, period=period, message=str(exc))
    if meta is None:
        base.status, base.message = "not_in_index", "This filing isn't in the company's recent-filings index. Open the filing instead."
        return base
    base.form, base.filed = meta["form"], meta["filed"]
    base.document_url = f"{accn_folder(cik, accn)}/{meta['primary_document']}"
    if not meta["inline_xbrl"]:
        base.status, base.message = "no_inline_xbrl", "This filing has no inline XBRL, so the figure can't be located in it. Open the filing."
        return base
    try:
        html = load_document(cik, accn, meta["primary_document"], fetch, cache_dir)
    except FilingFetchError as exc:
        too_large = "larger than" in str(exc)
        base.status = "too_large" if too_large else "fetch_failed"
        base.message = "The filing is too large to search here. Open the filing." if too_large else str(exc)
        return base
    occurrences, tag = find_in_document(html, concept, start, end)
    base.tag = tag
    base.occurrence_count = len(occurrences)
    base.occurrences = occurrences[:max_occurrences]
    if not occurrences:
        base.message = "The filing doesn't tag this figure for this period without a breakdown. Open the filing."
        return base
    base.status = "found"
    base.scale_words = _SCALE_WORDS.get(occurrences[0].scale)
    return base
