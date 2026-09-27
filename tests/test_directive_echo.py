"""
A conclusion that restates the directive is a structural failure, not an answer.

Why this file exists
    Counting stored runs on 2026-09-24, 7 of 10 v1.5.0 cross-agent conclusions opened
    with the directive's own block instruction ("Your final analysis, using only facts
    present in the Context..."), and every one passed the XML parse and was delivered.
    Replaying core/parsing.find_directive_echo over all 135 stored conclusions, each
    against the directive version that produced it, flagged 17: the 15 the manual
    marker count found, plus 2 NFLX runs under v1.3.0 that had copied that
    directive's own citation *example* ("$10.5B [SOURCE: SEC EDGAR 10-Q, ...
    CIK=0001065280]") as if it were a finding. It flagged none of the other 118.

    The fix has two halves, both pinned here:
      * detection: reject_unusable_conclusion turns an echo (or an empty conclusion)
        into StructuralParseError, so ADR-07 retries it and records PARSE_FAILURE;
      * prevention: directive v1.5.1 keeps nothing inside the template's tags, so
        there is no instruction text in the answer slot to copy.
"""

from __future__ import annotations

import unittest
import uuid

from core.directive import (
    DIRECTIVE_V1_3_0, DIRECTIVE_V1_5_0, DIRECTIVE_V1_5_1, DIRECTIVE_V1_5_2, get_active_directive,
)
from core.parsing import (
    AgentResponse,
    StructuralParseError,
    find_directive_echo,
    reject_unusable_conclusion,
)
from core.schemas import AgentID, ParseStatus
from pipeline.middleware import HaltError, run_validation_loop
from web.step_trace import StepTrace, wrap_adapter

# Real stored conclusions (trimmed), with the directive version that produced them.
ECHO_V150 = (
    "(Instruction — do not copy this into your response) Your final analysis, using only "
    "facts present in the Context and consistent with your thought_log. Cite per the "
    "CITATION RULE above. Never fabricate a URL."
)
ECHO_V130_EXAMPLE = (
    "Based on the provided Context, the revenue for NFLX in FY2025 Q2 is $10.5B "
    "[SOURCE: SEC EDGAR 10-Q, https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001065280]."
)
CLEAN = (
    "The movie Interstellar was released in 2014 "
    "[SOURCE: IMDB, https://www.imdb.com/title/tt0816692]."
)
CLEAN_INSUFFICIENT = (
    "The Context does not contain what is needed to answer this; the figures provided "
    "cover only the third quarter."
)


def _response(conclusion: str) -> AgentResponse:
    return AgentResponse(thought_log="reasoning", conclusion=conclusion, raw_text=f"<raw>{conclusion}</raw>")


class TestFindDirectiveEcho(unittest.TestCase):

    def test_real_v150_echo_is_found(self):
        self.assertIsNotNone(find_directive_echo(ECHO_V150, DIRECTIVE_V1_5_0.text))

    def test_copied_citation_example_is_found(self):
        self.assertIsNotNone(find_directive_echo(ECHO_V130_EXAMPLE, DIRECTIVE_V1_3_0.text))

    def test_clean_answers_are_not_flagged(self):
        for directive in (DIRECTIVE_V1_3_0, DIRECTIVE_V1_5_0, DIRECTIVE_V1_5_1):
            for text in (CLEAN, CLEAN_INSUFFICIENT):
                with self.subTest(version=directive.version, text=text[:30]):
                    self.assertIsNone(find_directive_echo(text, directive.text))

    def test_v151_description_echo_is_found(self):
        echoed = ("Your own answer to the question that was asked, using only facts present "
                  "in the Context and consistent with your thought_log.")
        self.assertIsNotNone(find_directive_echo(echoed, DIRECTIVE_V1_5_1.text))

    def test_match_is_word_for_word_not_fuzzy(self):
        # Punctuation and case differences don't hide a copy...
        self.assertIsNotNone(find_directive_echo(ECHO_V150.upper().replace(",", ""), DIRECTIVE_V1_5_0.text))
        # ...but a paraphrase is not treated as an echo.
        self.assertIsNone(find_directive_echo(
            "My analysis relies only on the Context and matches my reasoning.", DIRECTIVE_V1_5_0.text
        ))


class TestRejectUnusableConclusion(unittest.TestCase):

    def test_echo_raises_with_the_sentence_named(self):
        with self.assertRaises(StructuralParseError) as ctx:
            reject_unusable_conclusion(_response(ECHO_V150), DIRECTIVE_V1_5_0.text)
        self.assertIn("repeats the directive", str(ctx.exception))
        self.assertEqual(ctx.exception.raw_response, f"<raw>{ECHO_V150}</raw>")

    def test_empty_conclusion_raises(self):
        with self.assertRaises(StructuralParseError):
            reject_unusable_conclusion(_response("   "), DIRECTIVE_V1_5_1.text)

    def test_clean_conclusion_passes(self):
        reject_unusable_conclusion(_response(CLEAN), DIRECTIVE_V1_5_1.text)


def _sequence_adapter(*conclusions):
    remaining = list(conclusions)

    def adapter(subject, context, directive):
        return _response(remaining.pop(0))

    return adapter


class TestValidationLoopTreatsEchoAsStructuralFailure(unittest.TestCase):

    def _run(self, adapter):
        return run_validation_loop(
            "Interstellar release year", "", uuid.uuid4(), AgentID.EXTERNAL,
            directive=DIRECTIVE_V1_5_0, call_agent_fn=adapter,
        )

    def test_echo_then_clean_retry_succeeds_with_two_records(self):
        result = self._run(_sequence_adapter(ECHO_V150, CLEAN))
        self.assertEqual(
            [ro.parse_status for ro in result.reasoning_objects],
            [ParseStatus.PARSE_FAILURE, ParseStatus.SUCCESS],
        )
        self.assertEqual(result.final_response.conclusion, CLEAN)
        # The echoed attempt is kept as evidence, not discarded.
        self.assertIn("Your final analysis", result.reasoning_objects[0].raw_output["text"])

    def test_empty_twice_halts(self):
        with self.assertRaises(HaltError) as ctx:
            self._run(_sequence_adapter("", ""))
        self.assertEqual(
            [ro.parse_status for ro in ctx.exception.reasoning_objects],
            [ParseStatus.PARSE_FAILURE, ParseStatus.HALT],
        )

    def test_clean_first_attempt_is_untouched(self):
        result = self._run(_sequence_adapter(CLEAN))
        self.assertEqual([ro.parse_status for ro in result.reasoning_objects], [ParseStatus.SUCCESS])


class TestTraceLabelsTheEcho(unittest.TestCase):

    def test_wrapped_attempt_reads_parse_failure_with_reason(self):
        trace = StepTrace()
        wrapped = wrap_adapter(trace, "chat", _sequence_adapter(ECHO_V150), model_label="m")
        with self.assertRaises(StructuralParseError):
            wrapped("s", "c", DIRECTIVE_V1_5_0)
        step = trace.to_list()[0]
        self.assertEqual(step["status"], "parse_failure")
        self.assertIn("repeats the directive", step["error"])


class TestV151Template(unittest.TestCase):

    def test_active_directive_is_v152(self):
        # v1.6.0 (B4) was active briefly on 2026-09-26 and reverted: see core/directive.py.
        self.assertIs(get_active_directive(), DIRECTIVE_V1_5_2)

    def test_template_tags_are_empty(self):
        """The root-cause fix: no instruction text sits inside the answer slots (v1.5.1+)."""
        for d in (DIRECTIVE_V1_5_1, DIRECTIVE_V1_5_2):
            self.assertIn("<thought_log>\n</thought_log>", d.text)
            self.assertIn("<conclusion>\n</conclusion>", d.text)

    def test_v152_names_the_exact_closing_tags(self):
        # v1.5.1 first attempts closed with "[/conclusion]" 3 times in 22 (never before v1.5.1).
        self.assertIn("The closing tags are exactly </thought_log> and </conclusion>.", DIRECTIVE_V1_5_2.text)
        self.assertIn("never square brackets like [/conclusion]", DIRECTIVE_V1_5_2.text)

    def test_v152_changes_nothing_else(self):
        stripped = (DIRECTIVE_V1_5_2.text
                    .replace("- The closing tags are exactly </thought_log> and </conclusion>.\n", "")
                    .replace(", and close each block with its exact XML closing tag, </thought_log> and "
                             "</conclusion> (angle brackets and a forward slash — never square brackets "
                             "like [/conclusion])", ""))
        self.assertEqual(stripped, DIRECTIVE_V1_5_1.text)

    def test_keeps_grounding_and_citation_rules(self):
        for rule in ("GROUNDING RULE", "CITATION RULE", "insufficient context"):
            self.assertIn(rule, DIRECTIVE_V1_5_2.text)


if __name__ == "__main__":
    unittest.main()
