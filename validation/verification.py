"""
Claim verification — Week 7.

For each citation claim with a URL, fetches the source and checks whether
quantitative values from the same thought_log appear in it.

Supported source types:
  SEC EDGAR companyfacts JSON  (data.sec.gov/api/xbrl/*)
  Generic text / HTML          (regex number search)

verified=True   — source reachable, at least one claim number confirmed
verified=False  — source reachable, no claim number found in it; or the cited page
                  doesn't exist (HTTP 404/410); or the URL isn't one the agent was given
                  or retrieved
verified=None   — unattainable (blocked, timeout, other HTTP error, nothing to compare)

Each checked citation also carries `check`, saying which of those it was (CHECK_*), so
"the page doesn't exist" and "the site blocked the download" are never shown alike.

Retrieved first (added 2026-10-05, after run 03bd3ca5): when the caller passes the URLs
the agent actually saw (its search results, URLs in its Context, data sources the layer
fetched), a citation to any other URL is CHECK_NOT_RETRIEVED and is not fetched. The
agent can only have read what it was given; a URL outside that set was made up, however
real the site it names. In that run the model fused a site name with another result's
URL into a page that returns 404, and the old check reported it as "couldn't check".

verification_rate = verified_citations / total_citations  (0.0 when no citations)

ADR: stdlib only. No third-party HTTP or HTML libraries.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import replace

from core.numeric import QUANTITATIVE_RE, close_enough, normalize_number
from validation.claims import ExtractedClaim


_TIMEOUT_S = 10
_MAX_BYTES  = 300_000   # cap HTTP read at 300 KB

# What a citation's check found. Stored on the claim as `check`.
CHECK_CONFIRMED     = "confirmed"       # verified=True: a claim number is in the page
CHECK_NUMBER_ABSENT = "number_absent"   # verified=False: page read, no claim number in it
CHECK_PAGE_MISSING  = "page_missing"    # verified=False: HTTP 404/410, the page doesn't exist
CHECK_NOT_RETRIEVED = "not_retrieved"   # verified=False: not a URL the agent was given or found
CHECK_UNREACHABLE   = "unreachable"     # verified=None: blocked, timed out, other HTTP error
CHECK_NOTHING_TO_COMPARE = "nothing_to_compare"  # verified=None: no numbers on one side

_MISSING_STATUSES = {404, 410}
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\[\]()<>\"']+")


# ── Number normalisation ───────────────────────────────────────────────────────
# One implementation, in core/numeric.py, shared with validation/facts.py. The
# private names are kept so this module's call sites read as before.
_normalize = normalize_number
_close_enough = close_enough


# ── Fetching helpers ───────────────────────────────────────────────────────────

def _fetch_with_status(url: str) -> tuple[str | None, int | None]:
    """Fetch URL: (text body, None) on success, (None, HTTP status or None) on any error."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "accountability-layer/1.0 audit-research@project.local"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return resp.read(_MAX_BYTES).decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as exc:
        return None, exc.code
    except Exception:
        return None, None


def _fetch(url: str) -> str | None:
    """Fetch URL, return text body or None on any error."""
    return _fetch_with_status(url)[0]


def normalize_url(url: str) -> str:
    """
    A URL reduced to what identifies the page, for "is this the page the agent saw?":
    no scheme, no "www.", no fragment, no trailing slash, host lower-cased. The path and
    query are kept as written, since two pages on one site differ only there.
    """
    try:
        p = urllib.parse.urlsplit(url.strip().rstrip(".,;"))
    except ValueError:
        return url.strip().lower()
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/")
    return host + path + (f"?{p.query}" if p.query else "")


def urls_in_text(*texts: str | None) -> set[str]:
    """Every http(s) URL written in these texts, e.g. the Context an agent was given."""
    return {m.group(0).rstrip(".,;") for t in texts if t for m in _URL_IN_TEXT_RE.finditer(t)}


def _is_edgar(url: str) -> bool:
    return "sec.gov" in url or "edgar" in url.lower()


def _numbers_from_edgar(text: str) -> set[float]:
    """Extract all numeric fact values from an SEC EDGAR companyfacts JSON."""
    out: set[float] = set()
    try:
        obj = json.loads(text)
    except Exception:
        return out
    for ns in obj.get("facts", {}).values():
        for concept in ns.values():
            for unit_vals in concept.get("units", {}).values():
                for entry in unit_vals:
                    v = entry.get("val")
                    if isinstance(v, (int, float)):
                        out.add(float(v))
    return out


def _numbers_from_text(text: str) -> set[float]:
    """Extract normalised numbers from arbitrary text."""
    out: set[float] = set()
    for m in QUANTITATIVE_RE.finditer(text):
        n = _normalize(m.group(0))
        if n is not None:
            out.add(n)
    return out


# ── Core check ─────────────────────────────────────────────────────────────────

def _check_url(url: str, claim_numbers: set[float]) -> tuple[bool | None, str]:
    """(verified, check) for one cited URL the agent did see."""
    if not claim_numbers:
        return None, CHECK_NOTHING_TO_COMPARE

    text, status = _fetch_with_status(url)
    if status in _MISSING_STATUSES:
        return False, CHECK_PAGE_MISSING
    if not text or not text.strip():
        return None, CHECK_UNREACHABLE

    source_numbers = (
        _numbers_from_edgar(text)
        if _is_edgar(url) and text.strip().startswith("{")
        else _numbers_from_text(text)
    )

    if not source_numbers:
        return None, CHECK_NOTHING_TO_COMPARE

    for cn in claim_numbers:
        for sn in source_numbers:
            if _close_enough(cn, sn):
                return True, CHECK_CONFIRMED

    return False, CHECK_NUMBER_ABSENT


# ── Public API ─────────────────────────────────────────────────────────────────

def verify_claims(
    claims: list[ExtractedClaim],
    retrieved_urls: Iterable[str] | None = None,
) -> tuple[list[ExtractedClaim], float]:
    """
    For each citation claim with a URL, fetch the source and check whether
    quantitative values from the thought_log appear in it.

    `retrieved_urls`: every URL the agent was given or found (see the module docstring).
    None means the caller doesn't know them, and every cited URL is fetched as before;
    an empty collection means the agent saw no URLs, so any cited URL was not retrieved.

    Returns (updated_claims, verification_rate).
    verification_rate = verified_citations / total_citations  (0.0 if no citations)
    """
    quant_numbers: set[float] = set()
    for c in claims:
        if c.claim_type == "quantitative":
            n = _normalize(c.text)
            if n is not None:
                quant_numbers.add(n)

    citations = [
        c for c in claims
        if c.claim_type == "citation"
        and c.source_url
        and c.source_url not in ("N/A", "")
    ]

    if not citations:
        return claims, 0.0

    seen = None if retrieved_urls is None else {normalize_url(u) for u in retrieved_urls}

    # Check each distinct URL once
    url_results: dict[str, tuple[bool | None, str]] = {}
    for c in citations:
        url = c.source_url
        if url in url_results:
            continue
        if seen is not None and normalize_url(url) not in seen:
            url_results[url] = (False, CHECK_NOT_RETRIEVED)
        else:
            url_results[url] = _check_url(url, quant_numbers)

    verified_count = sum(1 for v, _ in url_results.values() if v is True)

    updated: list[ExtractedClaim] = []
    for c in claims:
        if c.claim_type == "citation" and c.source_url in url_results:
            verified, check = url_results[c.source_url]
            updated.append(replace(c, verified=verified, check=check))
        else:
            updated.append(c)

    rate = round(verified_count / len(citations), 3)
    return updated, rate
