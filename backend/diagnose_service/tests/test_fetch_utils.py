"""
Tests for shared._fetch_utils.extract_text_from_html(_with_method).

Both are pure functions over a raw HTML string: no network call, no
mocking needed, so these run the same locally and in CI.
"""

import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _THIS_DIR.parent  # backend/diagnose_service
_BACKEND_DIR = _SERVICE_DIR.parent  # backend/

sys.path.insert(0, str(_BACKEND_DIR))

from shared._fetch_utils import extract_text_from_html, extract_text_from_html_with_method  # noqa: E402

_HTML_WITH_NAV_BODY_FOOTER = """
<html><body>
<nav><a href="/">Home</a><a href="/about">About</a><a href="/contact">Contact</a></nav>
<header>CompanyName</header>
<main><article>
<h1>Our Product Helps Teams Ship Faster</h1>
<p>We built a platform that lets engineering teams track issues, plan sprints, and ship releases
without the usual overhead. Customers report cutting their release cycle time significantly after
adopting our workflow.</p>
<p>The product integrates with common version control and messaging tools, and supports custom
automation rules for repetitive tasks across the whole team.</p>
</article></main>
<footer>Copyright 2026 CompanyName. Privacy Policy. Terms of Service. All rights reserved.</footer>
</body></html>
"""

_HTML_TOO_SPARSE_FOR_TRAFILATURA = "<html><body><p>Hi.</p></body></html>"

# A homepage shape where the testimonial card itself is the evidence a
# caller like evidence_engine needs to see (named customer, a dollar
# figure, a percentage) — the exact kind of content trafilatura's
# article-focused extraction tends to drop as boilerplate.
_HTML_WITH_TESTIMONIAL_CARD = """
<html><body>
<nav><a href="/">Home</a><a href="/pricing">Pricing</a></nav>
<main><article>
<h1>Our Platform Helps Sales Teams Close Faster</h1>
<p>We built a CRM that combines calling, email, and pipeline management into one tool, so reps
spend less time switching between apps and more time talking to customers. The platform
integrates with common calling and messaging tools and supports custom automation for
repetitive follow-up tasks.</p>
</article>
<div class="testimonial">
"We've saved over $100,000 in tech costs and our booked appointments went up 50% since
switching." &mdash; Dana Example, Founder at ExampleCo
</div>
</main>
<footer>Copyright 2026. Privacy Policy.</footer>
</body></html>
"""


class ExtractTextFromHtmlTests(unittest.TestCase):
    def test_nav_and_footer_excluded_body_included(self):
        text, method = extract_text_from_html_with_method(_HTML_WITH_NAV_BODY_FOOTER)

        self.assertEqual(method, "trafilatura")
        self.assertIn("Our Product Helps Teams Ship Faster", text)
        self.assertIn("release cycle time", text)
        self.assertNotIn("Home", text)
        self.assertNotIn("About", text)
        self.assertNotIn("Copyright", text)
        self.assertNotIn("Privacy Policy", text)

    def test_sparse_content_falls_back_to_beautifulsoup(self):
        text, method = extract_text_from_html_with_method(_HTML_TOO_SPARSE_FOR_TRAFILATURA)

        self.assertEqual(method, "beautifulsoup_fallback")
        self.assertEqual(text, "Hi.")

    def test_empty_html_does_not_raise(self):
        text, method = extract_text_from_html_with_method("")

        self.assertEqual(text, "")
        self.assertEqual(method, "beautifulsoup_fallback")

    def test_deterministic_output_for_same_input(self):
        first = extract_text_from_html_with_method(_HTML_WITH_NAV_BODY_FOOTER)
        second = extract_text_from_html_with_method(_HTML_WITH_NAV_BODY_FOOTER)

        self.assertEqual(first, second)

    def test_extract_text_from_html_matches_the_text_half_of_with_method(self):
        text_only = extract_text_from_html(_HTML_WITH_NAV_BODY_FOOTER)
        text_with_method, _method = extract_text_from_html_with_method(_HTML_WITH_NAV_BODY_FOOTER)

        self.assertEqual(text_only, text_with_method)

    def test_full_method_keeps_the_testimonial_card(self):
        text, method = extract_text_from_html_with_method(_HTML_WITH_TESTIMONIAL_CARD, method="full")

        self.assertEqual(method, "beautifulsoup_full")
        self.assertIn("Dana Example", text)
        self.assertIn("$100,000", text)
        self.assertIn("50%", text)
        # "full" is still the whole-page BeautifulSoup extraction, so
        # non-content tags are still excluded.
        self.assertNotIn("Home", text)
        self.assertNotIn("Copyright", text)

    def test_full_method_never_uses_trafilatura(self):
        _text, method = extract_text_from_html_with_method(_HTML_WITH_NAV_BODY_FOOTER, method="full")

        self.assertEqual(method, "beautifulsoup_full")


if __name__ == "__main__":
    unittest.main()
