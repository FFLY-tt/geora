"""
Tests for technical_engine.check_technical_readability: a deterministic,
rule-based engine with no LLM call, so these just need robots.txt and
the homepage fetch mocked — no AI call to fake.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

_THIS_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _THIS_DIR.parent  # backend/diagnose_service
_BACKEND_DIR = _SERVICE_DIR.parent  # backend/

sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))

import engines.technical_engine as technical_engine  # noqa: E402

DOMAIN = "example.com"

_FAKE_PLAIN_FINDING = ({"what_we_found": "stub", "what_it_costs_you": "", "fix_it_today": ""}, [])


def _words(count: int) -> str:
    """A simple, natural-reading block of `count` words, safe for trafilatura's own content-quality heuristics."""
    sentence = "Our platform helps teams ship faster with clear, reliable, well tested workflows every day. "
    words_per_sentence = len(sentence.split())
    repeats = count // words_per_sentence + 1
    return " ".join((sentence * repeats).split()[:count])


_HTML_FULL_MARKS = f"""
<html><head>
<script type="application/ld+json">{{"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "Example Inc"}}</script>
</head><body>
<h1>Welcome to Example</h1>
<h2>Features</h2>
<h2>Pricing</h2>
<article><p>{_words(850)}</p></article>
</body></html>
"""

_HTML_ZERO_MARKS = """
<html><head></head><body>
<div id="root"></div>
<script src="/static/app.bundle.js"></script>
</body></html>
"""

_HTML_PARTIAL_MARKS = f"""
<html><head>
<script type="application/ld+json">{{"@type": "Organization", "name": "Example Inc"}}</script>
</head><body>
<h1>Our Product</h1>
<article><p>{_words(500)}</p></article>
</body></html>
"""

_HTML_GRAPH_NESTED = f"""
<html><head>
<script type="application/ld+json">{{"@context": "https://schema.org", "@graph": [{{"@type": "WebSite", "name": "Example"}}, {{"@type": "Product", "name": "Example Product"}}]}}</script>
</head><body>
<h1>Welcome to Example</h1>
<h2>Features</h2>
<h2>Pricing</h2>
<article><p>{_words(850)}</p></article>
</body></html>
"""

_HTML_TYPE_AS_ARRAY = f"""
<html><head>
<script type="application/ld+json">{{"@type": ["LocalBusiness", "Organization"], "name": "Example Inc"}}</script>
</head><body>
<h1>Welcome to Example</h1>
<h2>Features</h2>
<h2>Pricing</h2>
<article><p>{_words(850)}</p></article>
</body></html>
"""

_HTML_MALFORMED_PLUS_VALID_LD_JSON = f"""
<html><head>
<script type="application/ld+json">{{this is not valid json,,,</script>
<script type="application/ld+json">{{"@type": "FAQPage", "name": "Example Inc"}}</script>
</head><body>
<h1>Welcome to Example</h1>
<h2>Features</h2>
<h2>Pricing</h2>
<article><p>{_words(850)}</p></article>
</body></html>
"""

_HTML_MULTIPLE_H1 = f"""
<html><head></head><body>
<h1>Welcome to Example</h1>
<h1>A second h1, which real sites sometimes have</h1>
<h2>Features</h2>
<h2>Pricing</h2>
<article><p>{_words(850)}</p></article>
</body></html>
"""

_HTML_EMPTY_H1 = f"""
<html><head></head><body>
<h1></h1>
<h2>Features</h2>
<h2>Pricing</h2>
<article><p>{_words(850)}</p></article>
</body></html>
"""

_ROBOTS_TXT_ALLOW_ALL = ""

_ROBOTS_TXT_BLOCK_ALL = """
User-agent: GPTBot
Disallow: /
User-agent: PerplexityBot
Disallow: /
User-agent: ClaudeBot
Disallow: /
User-agent: Google-Extended
Disallow: /
"""

_ROBOTS_TXT_BLOCK_TWO = """
User-agent: GPTBot
Disallow: /
User-agent: ClaudeBot
Disallow: /
"""


class _FakeRobotsResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


def _run_with(html: str, robots_txt: str, robots_status: int = 200):
    with patch.object(technical_engine, "fetch_page_html", return_value=html), \
         patch.object(technical_engine.requests, "get", return_value=_FakeRobotsResponse(robots_txt, robots_status)), \
         patch.object(technical_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
        return technical_engine.check_technical_readability(DOMAIN)


class TechnicalEngineTests(unittest.TestCase):
    def test_full_marks(self):
        result = _run_with(_HTML_FULL_MARKS, _ROBOTS_TXT_ALLOW_ALL)

        self.assertEqual(result.score, 100)
        self.assertEqual(result.details["crawler_access"]["points"], 30)
        self.assertEqual(result.details["crawler_access"]["blocked_crawlers"], [])
        self.assertEqual(result.details["static_content"]["points"], 30)
        self.assertGreaterEqual(result.details["static_content"]["word_count"], 800)
        self.assertTrue(result.details["static_content"]["h1_has_text"])
        self.assertEqual(result.details["structured_data"]["points"], 20)
        self.assertEqual(result.details["structured_data"]["found_types"], ["SoftwareApplication"])
        self.assertEqual(result.details["heading_structure"]["points"], 20)
        self.assertEqual(result.details["heading_structure"]["h1_count"], 1)
        self.assertEqual(result.details["heading_structure"]["h2_count"], 2)

    def test_zero_marks(self):
        result = _run_with(_HTML_ZERO_MARKS, _ROBOTS_TXT_BLOCK_ALL)

        self.assertEqual(result.score, 0)
        self.assertEqual(result.details["crawler_access"]["points"], 0)
        self.assertEqual(
            sorted(result.details["crawler_access"]["blocked_crawlers"]),
            sorted(technical_engine.AI_CRAWLER_USER_AGENTS),
        )
        self.assertEqual(result.details["static_content"]["points"], 0)
        self.assertLess(result.details["static_content"]["word_count"], 300)
        self.assertFalse(result.details["static_content"]["h1_has_text"])
        self.assertEqual(result.details["structured_data"]["points"], 0)
        self.assertEqual(result.details["structured_data"]["found_types"], [])
        self.assertEqual(result.details["heading_structure"]["points"], 0)

    def test_partial_marks(self):
        result = _run_with(_HTML_PARTIAL_MARKS, _ROBOTS_TXT_BLOCK_TWO)

        # 2 of 4 crawlers allowed -> 15, ~500 words -> 15, weak type
        # (Organization) only -> 5, 1 h1 and 0 h2 -> 10. Total 45.
        self.assertEqual(result.details["crawler_access"]["points"], 15)
        self.assertEqual(sorted(result.details["crawler_access"]["blocked_crawlers"]), ["ClaudeBot", "GPTBot"])
        self.assertEqual(result.details["static_content"]["points"], 15)
        self.assertEqual(result.details["structured_data"]["points"], 5)
        self.assertEqual(result.details["structured_data"]["found_types"], ["Organization"])
        self.assertEqual(result.details["heading_structure"]["points"], 10)
        self.assertEqual(result.details["heading_structure"]["h1_count"], 1)
        self.assertEqual(result.details["heading_structure"]["h2_count"], 0)
        self.assertEqual(result.score, 45)

    def test_missing_robots_txt_treated_as_fully_allowed(self):
        result = _run_with(_HTML_FULL_MARKS, "", robots_status=404)

        self.assertEqual(result.details["crawler_access"]["points"], 30)
        self.assertEqual(result.details["crawler_access"]["blocked_crawlers"], [])

    def test_homepage_fetch_failure_returns_insufficient_data(self):
        with patch.object(technical_engine, "fetch_page_html", side_effect=requests.ConnectionError("boom")), \
             patch.object(technical_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
            result = technical_engine.check_technical_readability(DOMAIN)

        self.assertEqual(result.score, -1)
        self.assertEqual(result.details["status"], "insufficient_data")

    def test_type_nested_inside_graph_is_found(self):
        result = _run_with(_HTML_GRAPH_NESTED, _ROBOTS_TXT_ALLOW_ALL)

        # @graph has WebSite (weak) and Product (strong) -> strong wins -> full marks.
        self.assertEqual(result.details["structured_data"]["points"], 20)
        self.assertEqual(result.details["structured_data"]["found_types"], ["Product", "WebSite"])

    def test_type_as_array_is_found(self):
        result = _run_with(_HTML_TYPE_AS_ARRAY, _ROBOTS_TXT_ALLOW_ALL)

        # Organization (weak) found, LocalBusiness isn't a recognized type at all -> partial marks.
        self.assertEqual(result.details["structured_data"]["points"], 5)
        self.assertEqual(result.details["structured_data"]["found_types"], ["LocalBusiness", "Organization"])

    def test_malformed_ld_json_block_is_skipped_not_fatal(self):
        result = _run_with(_HTML_MALFORMED_PLUS_VALID_LD_JSON, _ROBOTS_TXT_ALLOW_ALL)

        # The broken block is silently skipped; the valid FAQPage block
        # right after it still counts, and nothing raises.
        self.assertEqual(result.details["structured_data"]["points"], 20)
        self.assertEqual(result.details["structured_data"]["found_types"], ["FAQPage"])

    def test_multiple_h1_still_earns_full_heading_points(self):
        result = _run_with(_HTML_MULTIPLE_H1, _ROBOTS_TXT_ALLOW_ALL)

        self.assertEqual(result.details["heading_structure"]["h1_count"], 2)
        self.assertEqual(result.details["heading_structure"]["points"], 20)

    def test_static_content_full_marks_for_800_plus_words(self):
        result = _run_with(_HTML_FULL_MARKS, _ROBOTS_TXT_ALLOW_ALL)

        self.assertEqual(result.details["static_content"]["points"], 30)

    def test_static_content_partial_marks_for_300_to_799_words(self):
        result = _run_with(_HTML_PARTIAL_MARKS, _ROBOTS_TXT_ALLOW_ALL)

        self.assertEqual(result.details["static_content"]["points"], 15)

    def test_static_content_zero_marks_for_js_only_page(self):
        # Content almost entirely loaded by client-side JavaScript: the
        # raw HTML has essentially no real text, only a mount point and
        # a script bundle -- exactly what GPTBot/ClaudeBot would see.
        result = _run_with(_HTML_ZERO_MARKS, _ROBOTS_TXT_ALLOW_ALL)

        self.assertEqual(result.details["static_content"]["points"], 0)
        self.assertLess(result.details["static_content"]["word_count"], 300)

    def test_static_content_full_page_has_real_h1_text(self):
        result = _run_with(_HTML_FULL_MARKS, _ROBOTS_TXT_ALLOW_ALL)

        self.assertTrue(result.details["static_content"]["h1_has_text"])

    def test_static_content_empty_h1_is_detected(self):
        result = _run_with(_HTML_EMPTY_H1, _ROBOTS_TXT_ALLOW_ALL)

        self.assertFalse(result.details["static_content"]["h1_has_text"])
        # The heading-structure check only counts the tag's presence,
        # so an empty h1 still earns its points there -- the two checks
        # measure different things on purpose.
        self.assertEqual(result.details["heading_structure"]["h1_count"], 1)

    def test_deterministic_output_for_same_input(self):
        first = _run_with(_HTML_PARTIAL_MARKS, _ROBOTS_TXT_BLOCK_TWO)
        second = _run_with(_HTML_PARTIAL_MARKS, _ROBOTS_TXT_BLOCK_TWO)

        self.assertEqual(first.score, second.score)
        self.assertEqual(first.details["crawler_access"], second.details["crawler_access"])
        self.assertEqual(first.details["static_content"], second.details["static_content"])
        self.assertEqual(first.details["structured_data"], second.details["structured_data"])
        self.assertEqual(first.details["heading_structure"], second.details["heading_structure"])


if __name__ == "__main__":
    unittest.main()
