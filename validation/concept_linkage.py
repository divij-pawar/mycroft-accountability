"""
Concept-aware number tagging — a prototype, not a replacement.

Why this exists
    divij/cross-agent-validation-disjoint-concepts-diagnosis.md found that 16 of 31
    real cross-agent runs flag as contradictions purely because Producer A
    (Assets/Revenues/NetIncomeLoss) and Producer B (EarningsPerShareDiluted/
    EarningsPerShareBasic/OperatingIncomeLoss) have disjoint concept vocabularies —
    a number present on one side and absent on the other is not evidence of
    disagreement when the two sides were never asked about the same thing. The
    diagnosis's own conclusion: fixing this needs "either tag extracted numbers
    with the concept they came from, or ... give the two producers some genuinely
    overlapping concepts." This module is the first half of that; it does not
    replace validation/cross_validation.py's contradiction_flag and is not called
    from web/server.py or anywhere in the production /api/compare path.

What it does
    Tags each quantitative token extracted from a conclusion with the known lens
    concept (Assets, Revenues, ...) whose keyword appears in the same sentence, by
    a cheap substring match — not a real entity linker. A number that matches no
    known concept keyword is left untagged.

The measured, honest result (see tests/test_concept_linkage.py)
    Excluding known-concept-tagged numbers from comparison (they can never be
    corroborated by a producer whose concept set is disjoint by construction —
    see producers/earnings.py's docstring) does kill every dollar-figure-vs-
    dollar-figure disjoint-concept false positive. It does NOT cleanly separate
    the remaining cases: an untagged bare ratio or percentage (asset turnover, ROA,
    a debt-to-equity ratio) is syntactically identical whether it is a legitimate
    derived metric one producer happened to compute, or a fabrication — the
    corpus's one confirmed true positive (AAPL's fabricated 0.34 debt-to-equity
    ratio) and several of its false positives (JNJ's ROA, GOOGL's ROA, PG's ratio)
    are all untagged numbers for the same reason. No rule operating on extracted
    text alone can tell those apart; that needs the number checked against its
    producer's own reported concepts (validation/verification.py's job, not this
    module's, and not currently wired into the cross-agent path either — see
    web/self_report.py's fabrication-not-caught entry).
"""

from __future__ import annotations

import re
from typing import Iterable

from core.numeric import QUANTITATIVE_RE, close_enough, normalize_number

# Concept -> keywords likely to appear in prose citing it. Each list includes
# both a natural-language phrasing AND the raw XBRL tag name (lowercased, no
# spaces) — the latter matters because producers/lens.py's context format is
# literally "ConceptName: value" (ConceptLens.summarize), and models frequently
# echo that exact tag name back verbatim (e.g. "EarningsPerShareDiluted (6.88)")
# rather than paraphrasing it. Missing this halved this prototype's hit rate on
# the real corpus during development — see tests/test_concept_linkage.py.
CONCEPT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Assets": ("asset", "assets"),
    "Revenues": ("revenue", "revenues"),
    "NetIncomeLoss": ("net income", "net loss", "netincomeloss"),
    "EarningsPerShareDiluted": (
        "diluted eps", "eps diluted", "diluted earnings per share",
        "earnings per share diluted", "earningspersharediluted",
    ),
    "EarningsPerShareBasic": (
        "basic eps", "eps basic", "basic earnings per share",
        "earnings per share basic", "earningspersharebasic",
    ),
    "OperatingIncomeLoss": (
        "operating income", "operating loss", "operatingincomeloss",
    ),
}

# Splits on a sentence-ending punctuation mark only when followed by whitespace
# — the same rule validation/claims.py's _sentences() uses. This matters more
# here than there: a naive "split on any '.'" breaks on the decimal point
# inside a number like "14.24 vs 14.41", truncating the sentence mid-figure and
# hiding a concept keyword that appeared earlier in it (found empirically —
# GOOGL's Diluted EPS pair (14.41 vs 14.24) only tagged the first number until
# this was fixed).
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+')


def _sentence_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, lowercased sentence) for every sentence in `text`, in order."""
    spans: list[tuple[int, int, str]] = []
    pos = 0
    for piece in _SENTENCE_SPLIT_RE.split(text):
        if not piece:
            continue
        start = text.index(piece, pos)
        end = start + len(piece)
        spans.append((start, end, piece.lower()))
        pos = end
    return spans


def _nearest_concept(sentence: str, number_pos: int) -> str | None:
    """
    The concept whose keyword occurrence is closest (by character distance) to
    `number_pos` within `sentence` — not just the first concept in dict order.

    Matters when a sentence names two sibling concepts together, e.g. "the
    difference between EarningsPerShareDiluted (6.88) and
    EarningsPerShareBasic (6.91)": both numbers' sentence contains both
    keywords, so proximity — not iteration order — is what tells 6.88 apart
    from 6.91.
    """
    # Distance is measured from the END of the keyword occurrence, not its
    # start: concept names precede their value ("EarningsPerShareDiluted
    # (6.88)", "Diluted EPS of 6.88"), so the keyword's closing edge is what
    # sits next to the number. Measuring from the start would make a long
    # keyword (e.g. "earningspersharebasic", 22 chars) look farther from a
    # number it immediately precedes than a short one — a real bug caught by
    # tests/test_concept_linkage.py's Diluted/Basic EPS regression case.
    best_concept: str | None = None
    best_distance: int | None = None
    for name, keywords in CONCEPT_KEYWORDS.items():
        for kw in keywords:
            start = sentence.find(kw)
            while start != -1:
                distance = abs((start + len(kw)) - number_pos)
                if best_distance is None or distance < best_distance:
                    best_distance, best_concept = distance, name
                start = sentence.find(kw, start + 1)
    return best_concept


def tag_numbers(conclusion: str) -> list[tuple[str, str | None]]:
    """
    Every quantitative token in `conclusion`, paired with the concept whose
    keyword occurs closest to it within the same sentence, or None if no known
    concept keyword is present there.

    None does not mean "unimportant" — it means "this prototype's cheap keyword
    match found no known concept nearby." That bucket contains both legitimate
    derived ratios and outright fabrications, indistinguishably; see the module
    docstring.
    """
    spans = _sentence_spans(conclusion)
    tagged: list[tuple[str, str | None]] = []
    for m in QUANTITATIVE_RE.finditer(conclusion):
        sent_start, sent_end, sentence = next(
            (
                (start, end, s) for start, end, s in spans
                if start <= m.start() < end
            ),
            (0, len(conclusion), conclusion.lower()),
        )
        concept = _nearest_concept(sentence, m.start() - sent_start)
        tagged.append((m.group(0).strip().lower(), concept))
    return tagged


# Per-share figures are reported to the cent; everything else a lens carries is a
# large dollar amount, where rounding to "$93.7 billion" must still agree.
_PER_SHARE_CONCEPTS = frozenset({"EarningsPerShareDiluted", "EarningsPerShareBasic"})


def _same_value(concept: str, a: float, b: float) -> bool:
    if concept in _PER_SHARE_CONCEPTS:
        return abs(a - b) <= 0.005 + 1e-12
    return close_enough(a, b, 0.005)


def contradiction_flag_concept_aware(
    a_conclusion: str,
    b_conclusion: str,
    *,
    shared_concepts: Iterable[str] = (),
) -> tuple[bool, list[str], dict[str, list[tuple[str, str | None]]]]:
    """
    Prototype alternative to validation/cross_validation.py's symmetric-difference
    rule over ALL extracted numbers.

    A number tagged with a concept only ONE lens reads is excluded from comparison
    entirely — the other side was never given it, so its absence there is not
    evidence of anything. An untagged number keeps the old presence/absence rule.

    shared_concepts (B2, 2026-09-25): concepts both agents were handed (lens v2
    shares NetIncomeLoss and EarningsPerShareDiluted; the caller derives the set
    from the lens definitions — producers.lens.shared_concepts — rather than this
    module assuming the lenses are disjoint). For each shared concept both agents
    cite, the values are compared: if no value one agent gives for it matches any
    value the other gives (to the cent for EPS, within 0.5% otherwise), those
    numbers are divergent. A shared concept only one agent cites is not flagged —
    that agent simply didn't mention it. Periods are NOT considered here (a
    quarterly and an annual net income would read as a conflict); that is
    canonical_facts' job. The default, no shared concepts, is the v1 behaviour
    every stored-corpus test pins.

    Returns (flag, divergent_numbers, {"a": tagged_a, "b": tagged_b}) — the
    tagging is returned too so a caller (or a test) can inspect *why*.
    """
    a_tagged = tag_numbers(a_conclusion)
    b_tagged = tag_numbers(b_conclusion)
    shared = frozenset(shared_concepts)

    a_untagged = {num for num, concept in a_tagged if concept is None}
    b_untagged = {num for num, concept in b_tagged if concept is None}
    divergent = set(a_untagged.symmetric_difference(b_untagged))

    for concept in sorted(shared):
        a_vals = [(n, normalize_number(n)) for n, c in a_tagged if c == concept]
        b_vals = [(n, normalize_number(n)) for n, c in b_tagged if c == concept]
        a_vals = [(n, v) for n, v in a_vals if v is not None]
        b_vals = [(n, v) for n, v in b_vals if v is not None]
        if not a_vals or not b_vals:
            continue
        if not any(_same_value(concept, va, vb) for _, va in a_vals for _, vb in b_vals):
            divergent.update(n for n, _ in a_vals + b_vals)

    divergent_sorted = sorted(divergent)
    return bool(divergent_sorted), divergent_sorted, {"a": a_tagged, "b": b_tagged}
