"""The text written for customers must not use our internal words or the punctuation the page rules forbid."""

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from shared._report_style_guide import PLAIN_LANGUAGE_SYSTEM_PROMPT  # noqa: E402
from shared._text_format import find_banned_formatting, find_report_violations, sanitize_banned_formatting  # noqa: E402


class ReportTextRuleTests(unittest.TestCase):
    def test_internal_words_are_violations(self):
        for word in ("gap", "citation", "unknown", "layer", "reliance", "presence", "coverage", "method", "version", "cache", "engine", "judgment", "beta"):
            self.assertTrue(
                any("internal word" in issue for issue in find_report_violations(f"We found a {word} on your site.")), word
            )
        self.assertTrue(any("internal word" in issue for issue in find_report_violations("The confidence interval is wide.")))

    def test_ordinary_text_is_clean(self):
        self.assertEqual(find_report_violations("AI search tools often skip your pricing page. Customers pick a competitor."), [])
        self.assertEqual(find_report_violations("Engineering teams and presentations are fine words."), [])

    def test_en_dash_bar_and_braces_are_banned_formatting(self):
        for text in ("A – B", "A | B", "A {b}"):
            self.assertTrue(find_banned_formatting(text), text)

    def test_the_last_resort_cleanup_removes_them(self):
        cleaned = sanitize_banned_formatting("A gap – in the citation | layer {x} is unknown (really)")
        self.assertEqual(find_report_violations(cleaned), [])
        self.assertIn("problem", cleaned)

    def test_the_writing_prompt_lists_the_internal_words_and_does_not_use_them(self):
        self.assertIn("internal vocabulary", PLAIN_LANGUAGE_SYSTEM_PROMPT)
        self.assertNotIn("visibility gap", PLAIN_LANGUAGE_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
