r"""
Quantitative-token extraction — one definition of "a number", for everyone.

This pattern existed in three places: `validation/claims.py` as
`_QUANTITATIVE_RE`, `validation/consistency.py` as `_NUMBER_RE`, and
`validation/verification.py` as another `_NUMBER_RE`. All three were verbatim
copies, and each carried a comment instructing the reader to keep it in sync with
the other two — which is a design telling on itself.

Hand-syncing three regexes fails in a specific and damaging way here, because all
three feed *evidence* rather than presentation:

  * `claims.py` decides which figures get extracted as quantitative claims;
  * `consistency.py` decides which figures count when scoring two runs of one
    agent against each other, and its `_extract_numbers` is what
    `cross_validation.py` uses to find numeric divergence between two agents;
  * `verification.py` decides which figures get checked against the source.

If one copy drifts, the system reports a claim it never verifies, or calls two
conclusions identical over a number one of them cited and the other did not. Both
outcomes are P3 violations — a number in a report that no consistent rule
produced — and neither would raise an error.

The widening to bare decimals is deliberate and lossy: `\b\d+\.\d+\b` matches
"0.34" (the real fabricated debt-to-equity ratio that went unextracted on
2026-08-29 because it had no $/%/x/bps suffix) but also matches a section number
like "2.1" or a version string. That false-positive tradeoff was accepted once,
here, rather than three times in three files.

2026-09-11 fix — comma-grouped numbers with no suffix at all: `\d+` cannot cross
a comma, so a real figure with no `$`/`%`/`x`/`bps` prefix and no decimal-only
shape — a model echoing a raw XBRL value like "13,971,000,000.0" — fell all the
way to the bare-decimal alternative, which could only match past the *last*
comma: the extracted "number" for a $13.971 billion figure was the string
"000.0". Found by replaying real stored runs into `tests/fixtures/
cross_agent_real_runs_corpus.json` (run `515f263a`, ticker V) — see
`web/self_report.py`'s `regex-truncates-comma-grouped-numbers` entry and
`divij/cross-agent-validation-disjoint-concepts-diagnosis.md` §3b for the
discovery. Fixed by a dedicated comma-grouped alternative, tried before the
bare-decimal fallback so a fully-grouped number is taken whole.
"""

from __future__ import annotations

import re

# Every alternative that counts as a quantitative token. Order matters only for
# which alternative wins on an overlapping match; the $-prefixed form is first so
# "$383.266 billion" is taken whole rather than as a bare decimal, and the
# comma-grouped form comes before the bare-decimal fallback so a number like
# "13,971,000,000.0" is taken whole rather than truncated to its last group.
QUANTITATIVE_RE = re.compile(
    r'(?:'
    r'\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|trillion|M|B|T))?'
    r'|[\d,]+(?:\.\d+)?\s*%'
    r'|[\d,]+(?:\.\d+)?x'           # multiples e.g. 2.3x
    r'|[\d,]+(?:\.\d+)?\s*bps'      # basis points
    r'|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b'  # comma-grouped, no $/%/x/bps prefix or
                                           # suffix at all — a raw XBRL value a
                                           # model echoed verbatim, e.g.
                                           # "13,971,000,000.0". Requires proper
                                           # 3-digit grouping, so it never fires
                                           # on a bare short integer.
    r'|\b\d+\.\d+\b'                # bare decimal ratios: debt-to-equity, EPS, asset
                                     # turnover — no unit suffix. See the module
                                     # docstring for the accepted false positives and
                                     # divij/model-test-report-2026-08-29.md, Tests 1 and 5.
    r')',
    re.IGNORECASE,
)

# Order-of-magnitude suffixes, shared by every caller that has to compare a prose
# figure against a raw source value.
SUFFIX_MAP: dict[str, float] = {
    "trillion": 1e12, "t": 1e12,
    "billion":  1e9,  "b": 1e9,
    "million":  1e6,  "m": 1e6,
}


def extract_numbers(text: str) -> list[str]:
    """
    Every quantitative token in `text`, normalised for set comparison
    (stripped and lowercased), in order of appearance and *with duplicates kept*.

    Callers that need de-duplication do it themselves — `claims.py` de-duplicates
    by (type, value) so the same figure restated twice is one claim, while
    `consistency.py` builds a set. Doing it here would take that choice away from
    both.
    """
    return [m.group(0).strip().lower() for m in QUANTITATIVE_RE.finditer(text)]


def normalize_number(token: str) -> float | None:
    """
    Parse a quantitative token to a plain float, or None: "$383.266 billion" ->
    383266000000.0, "13,971,000,000.0" -> 13971000000.0, "106%" -> 106.0.

    Moved here from validation/verification.py (was its private _normalize) so the
    cross-agent comparator (validation/facts.py) and claim verification normalize
    figures identically — two copies would let them disagree about what "$4.2M" is.
    """
    s = token.strip().replace(",", "").replace("$", "")
    lower = s.lower()
    for suffix, mult in SUFFIX_MAP.items():
        if lower.endswith(suffix):
            try:
                return float(lower[: -len(suffix)].strip()) * mult
            except ValueError:
                return None
    s = re.sub(r"[%xbps]+$", "", s, flags=re.IGNORECASE).strip()
    try:
        return float(s)
    except ValueError:
        return None


def close_enough(a: float, b: float, tol: float = 0.01) -> bool:
    """True if a and b agree within `tol` relative tolerance (default 1%)."""
    if a == 0.0 and b == 0.0:
        return True
    denom = max(abs(a), abs(b))
    return denom > 0 and abs(a - b) / denom <= tol
