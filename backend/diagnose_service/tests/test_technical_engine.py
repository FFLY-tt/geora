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

_HTML_FULL_MARKS = """
<html><head>
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Organization", "name": "Example Inc"}</script>
</head><body>
<h1>Welcome to Example</h1>
<h2>Features</h2>
<h2>Pricing</h2>
<p>Some real content about the product.</p>
</body></html>
"""

_HTML_ZERO_MARKS = """
<html><head></head><body>
<p>No headings here, just a paragraph.</p>
</body></html>
"""

_HTML_PARTIAL_MARKS = """
<html><head>
<script type="application/ld+json">{"@type": "Product", "name": "Widget"}</script>
</head><body>
<h1>Our Product</h1>
<p>Only one heading level here.</p>
</body></html>
"""

_HTML_GRAPH_NESTED = """
<html><head>
<script type="application/ld+json">{"@context": "https://schema.org", "@graph": [{"@type": "WebSite", "name": "Example"}, {"@type": "Organization", "name": "Example Inc"}]}</script>
</head><body>
<h1>Welcome to Example</h1>
<h2>Features</h2>
<h2>Pricing</h2>
</body></html>
"""

_HTML_TYPE_AS_ARRAY = """
<html><head>
<script type="application/ld+json">{"@type": ["LocalBusiness", "Organization"], "name": "Example Inc"}</script>
</head><body>
<h1>Welcome to Example</h1>
<h2>Features</h2>
<h2>Pricing</h2>
</body></html>
"""

_HTML_MALFORMED_PLUS_VALID_LD_JSON = """
<html><head>
<script type="application/ld+json">{this is not valid json,,,</script>
<script type="application/ld+json">{"@type": "Organization", "name": "Example Inc"}</script>
</head><body>
<h1>Welcome to Example</h1>
<h2>Features</h2>
<h2>Pricing</h2>
</body></html>
"""

_HTML_MULTIPLE_H1 = """
<html><head></head><body>
<h1>Welcome to Example</h1>
<h1>A second h1, which real sites sometimes have</h1>
<h2>Features</h2>
<h2>Pricing</h2>
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
        self.assertEqual(result.details["crawler_access"]["points"], 40)
        self.assertEqual(result.details["crawler_access"]["blocked_crawlers"], [])
        self.assertEqual(result.details["structured_data"]["points"], 30)
        self.assertEqual(result.details["structured_data"]["found_types"], ["Organization"])
        self.assertEqual(result.details["heading_structure"]["points"], 30)
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
        self.assertEqual(result.details["structured_data"]["points"], 0)
        self.assertEqual(result.details["structured_data"]["found_types"], [])
        self.assertEqual(result.details["heading_structure"]["points"], 0)

    def test_partial_marks(self):
        result = _run_with(_HTML_PARTIAL_MARKS, _ROBOTS_TXT_BLOCK_TWO)

        # 2 of 4 crawlers allowed -> 20, structured data found -> 30,
        # exactly 1 h1 -> 15, 0 h2 -> 0. Total 65.
        self.assertEqual(result.details["crawler_access"]["points"], 20)
        self.assertEqual(sorted(result.details["crawler_access"]["blocked_crawlers"]), ["ClaudeBot", "GPTBot"])
        self.assertEqual(result.details["structured_data"]["points"], 30)
        self.assertEqual(result.details["structured_data"]["found_types"], ["Product"])
        self.assertEqual(result.details["heading_structure"]["points"], 15)
        self.assertEqual(result.details["heading_structure"]["h1_count"], 1)
        self.assertEqual(result.details["heading_structure"]["h2_count"], 0)
        self.assertEqual(result.score, 65)

    def test_missing_robots_txt_treated_as_fully_allowed(self):
        result = _run_with(_HTML_FULL_MARKS, "", robots_status=404)

        self.assertEqual(result.details["crawler_access"]["points"], 40)
        self.assertEqual(result.details["crawler_access"]["blocked_crawlers"], [])

    def test_homepage_fetch_failure_returns_insufficient_data(self):
        with patch.object(technical_engine, "fetch_page_html", side_effect=requests.ConnectionError("boom")), \
             patch.object(technical_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
            result = technical_engine.check_technical_readability(DOMAIN)

        self.assertEqual(result.score, -1)
        self.assertEqual(result.details["status"], "insufficient_data")

    def test_type_nested_inside_graph_is_found(self):
        result = _run_with(_HTML_GRAPH_NESTED, _ROBOTS_TXT_ALLOW_ALL)

        self.assertEqual(result.details["structured_data"]["points"], 30)
        self.assertEqual(result.details["structured_data"]["found_types"], ["Organization", "WebSite"])

    def test_type_as_array_is_found(self):
        result = _run_with(_HTML_TYPE_AS_ARRAY, _ROBOTS_TXT_ALLOW_ALL)

        self.assertEqual(result.details["structured_data"]["points"], 30)
        self.assertEqual(result.details["structured_data"]["found_types"], ["LocalBusiness", "Organization"])

    def test_malformed_ld_json_block_is_skipped_not_fatal(self):
        result = _run_with(_HTML_MALFORMED_PLUS_VALID_LD_JSON, _ROBOTS_TXT_ALLOW_ALL)

        # The broken block is silently skipped; the valid one right
        # after it still counts, and nothing raises.
        self.assertEqual(result.details["structured_data"]["points"], 30)
        self.assertEqual(result.details["structured_data"]["found_types"], ["Organization"])

    def test_multiple_h1_still_earns_full_heading_points(self):
        result = _run_with(_HTML_MULTIPLE_H1, _ROBOTS_TXT_ALLOW_ALL)

        self.assertEqual(result.details["heading_structure"]["h1_count"], 2)
        self.assertEqual(result.details["heading_structure"]["points"], 30)

    def test_deterministic_output_for_same_input(self):
        first = _run_with(_HTML_PARTIAL_MARKS, _ROBOTS_TXT_BLOCK_TWO)
        second = _run_with(_HTML_PARTIAL_MARKS, _ROBOTS_TXT_BLOCK_TWO)

        self.assertEqual(first.score, second.score)
        self.assertEqual(first.details["crawler_access"], second.details["crawler_access"])
        self.assertEqual(first.details["structured_data"], second.details["structured_data"])
        self.assertEqual(first.details["heading_structure"], second.details["heading_structure"])


if __name__ == "__main__":
    unittest.main()
