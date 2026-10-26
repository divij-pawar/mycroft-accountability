"""
Tests for core/numeric.py — the single quantitative pattern.

Two jobs. First, pin the pattern's behaviour, including the false positives it
knowingly accepts, so a future "tightening" has to be a deliberate change to an
asserted expectation rather than a silent one.

Second, and the reason this file exists at all: assert that the three modules that
used to carry verbatim copies of this regex now share one object. Behavioural tests
cannot catch that regression — three identical copies pass every behavioural test
there is, right up to the moment one of them is edited. So the identity check is
the test that would actually have failed before this was consolidated.
"""

import re
import unittest

from core.numeric import QUANTITATIVE_RE, SUFFIX_MAP, extract_numbers


class TestSingleSource(unittest.TestCase):
    def test_validation_modules_share_one_pattern_object(self):
        import validation.claims as claims
        import validation.consistency as consistency
        import validation.verification as verification

        self.assertIs(claims.QUANTITATIVE_RE, QUANTITATIVE_RE)
        self.assertIs(verification.QUANTITATIVE_RE, QUANTITATIVE_RE)
        # consistency imports the function rather than the pattern, because
        # cross_validation.py depends on _extract_numbers by name.
        self.assertIs(consistency.extract_numbers, extract_numbers)

    def test_no_module_defines_its_own_copy(self):
        """The old private names are gone, so a re-added copy is visible in review."""
        import validation.claims as claims
        import validation.consistency as consistency
        import validation.verification as verification

        for module in (claims, consistency, verification):
            self.assertFalse(
                hasattr(module, "_NUMBER_RE"),
                f"{module.__name__} re-introduced a private number regex",
            )
            self.assertFalse(
                hasattr(module, "_QUANTITATIVE_RE"),
                f"{module.__name__} re-introduced a private quantitative regex",
            )

    def test_cross_validation_still_reaches_the_shared_extractor(self):
        """
        The comparator's numeric divergence check runs on this pattern. If that link
        breaks, two agents can be called identical over a number one of them cited.
        """
        from validation.cross_validation import _extract_numbers

        self.assertEqual(_extract_numbers("EPS was 14.41"), ["14.41"])


class TestPatternBehaviour(unittest.TestCase):
    def test_dollar_amount_with_magnitude_taken_whole(self):
        self.assertEqual(
            extract_numbers("Revenue was $383.266 billion this year."),
            ["$383.266 billion"],
        )

    def test_percent_multiple_and_bps(self):
        self.assertEqual(
            extract_numbers("Up 12.5% at 2.3x book with a 150 bps spread."),
            ["12.5%", "2.3x", "150 bps"],
        )

    def test_bare_decimal_is_matched(self):
        """The 2026-08-29 fabricated debt-to-equity ratio had no unit suffix."""
        self.assertEqual(
            extract_numbers("Debt-to-equity ratio of 0.34."), ["0.34"]
        )

    def test_bare_integer_is_not_matched(self):
        """Deliberate: unsuffixed integers are far more often prose than a figure."""
        self.assertEqual(extract_numbers("We reviewed 12 filings."), [])

    def test_accepted_false_positives_are_asserted_not_assumed(self):
        """
        A section number and a version string both match. This is the cost of
        catching suffix-free ratios, recorded as an expectation so it stays a
        known tradeoff rather than a surprise.
        """
        self.assertEqual(extract_numbers("See section 2.1 of version 3.11."), ["2.1", "3.11"])

    def test_normalisation_lowercases_and_strips(self):
        self.assertEqual(extract_numbers("Roughly $4.2 Billion."), ["$4.2 billion"])

    def test_duplicates_are_preserved_for_the_caller_to_decide(self):
        self.assertEqual(extract_numbers("12.5% then 12.5% again"), ["12.5%", "12.5%"])

    def test_suffix_map_covers_every_magnitude_the_pattern_matches(self):
        """
        verification.py normalises a matched figure using SUFFIX_MAP. Any magnitude
        word the pattern can match but the map cannot expand would silently
        normalise to None and drop out of verification.
        """
        matched_words = re.findall(r"[a-z]+", "million billion trillion m b t")
        for word in matched_words:
            self.assertIn(word, SUFFIX_MAP)


class TestSuffixBoundary(unittest.TestCase):
    """A magnitude suffix must end a word (2026-10-05). Without the boundary, the first letter of the
    next word was read as trillion, billion or million."""

    def test_a_following_word_is_not_a_magnitude(self):
        for text, token in (("EPS was $2.03 this quarter.", "$2.03"), ("$10 total", "$10"),
                            ("$2.02 basic", "$2.02"), ("revenue of $5 thousand", "$5")):
            self.assertEqual(extract_numbers(text), [token.lower()], text)

    def test_real_suffixes_still_scale(self):
        from core.numeric import normalize_number
        for text, value in (("$94.9B in Q3", 94.9e9), ("$5M, up", 5e6), ("$1.2 tn", 1.2e12),
                            ("$4 bn and", 4e9), ("$29.79 billion.", 29.79e9), ("$3 trillion", 3e12)):
            (token,) = extract_numbers(text)
            self.assertAlmostEqual(normalize_number(token), value, delta=value * 1e-9, msg=text)


if __name__ == "__main__":
    unittest.main()
