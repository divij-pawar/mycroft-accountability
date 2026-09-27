"""
Claim extraction tests — no prior test file existed for validation/claims.py before this.

Scoped primarily around the 2026-08-29 regex widening (see validation/claims.py's _QUANTITATIVE_RE
comment and divij/model-test-report-2026-08-29.md, Test 1): a real fabricated ratio
("debt-to-equity ratio of 0.34") went unextracted because it had no $/%/x/bps suffix.
"""

import unittest

from validation.claims import ExtractedClaim, extract_claims


class TestCitationExtraction(unittest.TestCase):

    def test_extracts_label_and_url(self):
        claims = extract_claims("Revenue grew. [SOURCE: 10-K 2025, https://www.sec.gov/x]")
        citations = [c for c in claims if c.claim_type == "citation"]
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0].source_label, "10-K 2025")
        self.assertEqual(citations[0].source_url, "https://www.sec.gov/x")

    def test_duplicate_citations_deduped(self):
        text = "[SOURCE: 10-K, https://a] and again [SOURCE: 10-K, https://b]"
        citations = [c for c in extract_claims(text) if c.claim_type == "citation"]
        self.assertEqual(len(citations), 1)


class TestQuantitativeExtraction(unittest.TestCase):
    """Regression coverage for the pre-existing suffixed patterns, plus the new bare-decimal case."""

    def test_dollar_amount_with_unit_word(self):
        claims = extract_claims("Net income of $101.46 billion this quarter.")
        quant = [c.text for c in claims if c.claim_type == "quantitative"]
        self.assertIn("$101.46 billion", quant)

    def test_percentage(self):
        claims = extract_claims("Net profit margin of 38.0% this quarter.")
        quant = [c.text for c in claims if c.claim_type == "quantitative"]
        self.assertIn("38.0%", quant)

    def test_multiple_suffix(self):
        claims = extract_claims("Trading at 2.3x book value.")
        quant = [c.text for c in claims if c.claim_type == "quantitative"]
        self.assertIn("2.3x", quant)

    def test_basis_points(self):
        claims = extract_claims("Spread widened by 50bps.")
        quant = [c.text for c in claims if c.claim_type == "quantitative"]
        self.assertIn("50bps", quant)

    def test_bare_decimal_ratio_now_extracted(self):
        # This is the exact real case from the live AAPL run that motivated the fix:
        # a fabricated ratio with no unit suffix was previously invisible to extraction.
        claims = extract_claims("Calculated the debt-to-equity ratio as 0.34.")
        quant = [c.text for c in claims if c.claim_type == "quantitative"]
        self.assertIn("0.34", quant)

    def test_bare_decimal_eps_pair_both_extracted(self):
        # The GOOGL live-run case: two bare decimals in one sentence, both must be caught.
        claims = extract_claims("Basic and Diluted EPS (14.41 vs 14.24) are close.")
        quant = [c.text for c in claims if c.claim_type == "quantitative"]
        self.assertIn("14.41", quant)
        self.assertIn("14.24", quant)

    def test_bare_integer_not_extracted(self):
        # The widened pattern requires an actual decimal point (\d+\.\d+) specifically so a
        # bare integer (a year, a count) doesn't turn into a false quantitative claim.
        claims = extract_claims("Filed in 2025 under the new policy.")
        quant = [c.text for c in claims if c.claim_type == "quantitative"]
        self.assertEqual(quant, [])

    def test_known_false_positive_tradeoff_is_accepted_not_hidden(self):
        # Documented tradeoff (see validation/claims.py's comment): a bare decimal has no unit, so a
        # non-financial decimal (a section reference) is indistinguishable from a ratio.
        # This test exists so the tradeoff is a named, asserted behavior — not a silent bug
        # someone "fixes" by accident later without realizing it was a deliberate choice.
        claims = extract_claims("As described in Section 2.1 of the filing.")
        quant = [c.text for c in claims if c.claim_type == "quantitative"]
        self.assertIn("2.1", quant)


class TestHedgeAndCausal(unittest.TestCase):

    def test_hedge_word_detected(self):
        claims = extract_claims("The figure is approximately correct but unverified.")
        hedges = [c for c in claims if c.claim_type == "hedge"]
        self.assertEqual(len(hedges), 1)

    def test_causal_phrase_detected(self):
        claims = extract_claims("Revenue fell because demand weakened.")
        causal = [c for c in claims if c.claim_type == "causal"]
        self.assertEqual(len(causal), 1)


class TestEmptyInput(unittest.TestCase):

    def test_none_returns_empty_list(self):
        self.assertEqual(extract_claims(None), [])

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(extract_claims(""), [])


class TestExtractedClaimSerialisation(unittest.TestCase):

    def test_to_dict_omits_none_source_fields(self):
        claims = extract_claims("Net income of $101.46 billion.")
        quant = [c for c in claims if c.claim_type == "quantitative"][0]
        d = quant.to_dict()
        self.assertNotIn("source_label", d)
        self.assertNotIn("source_url", d)


if __name__ == "__main__":
    unittest.main()
