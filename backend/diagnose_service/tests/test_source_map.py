"""
Tests for _source_map.build_source_map: a pure, offline function, so
none of this needs any mocking or network access.
"""

import sys
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _THIS_DIR.parent  # backend/diagnose_service

sys.path.insert(0, str(_SERVICE_DIR))

from engines._source_map import build_source_map  # noqa: E402


class SourceMapTests(unittest.TestCase):
    def test_www_prefix_treated_as_same_domain(self):
        answers = [
            {"query": "q1", "citations": ["https://www.g2.com/products/x"]},
            {"query": "q2", "citations": ["https://g2.com/products/y"]},
        ]
        result = build_source_map("example.com", answers)

        self.assertEqual(result["unique_domains"], 1)
        self.assertEqual(result["top_domains"][0]["domain"], "g2.com")
        self.assertEqual(result["top_domains"][0]["answers_citing"], 2)

    def test_same_domain_multiple_pages_in_one_answer_counts_once(self):
        answers = [
            {
                "query": "q1",
                "citations": [
                    "https://g2.com/page-a",
                    "https://g2.com/page-b",
                    "https://www.g2.com/page-c",
                ],
            },
        ]
        result = build_source_map("example.com", answers)

        self.assertEqual(result["top_domains"][0]["domain"], "g2.com")
        self.assertEqual(result["top_domains"][0]["answers_citing"], 1)
        self.assertEqual(result["top_domains"][0]["questions_citing"], 1)

    def test_own_domain_subdomain_vs_similar_domain(self):
        answers = [
            {"query": "q1", "citations": ["https://blog.example.com/post"]},
            {"query": "q2", "citations": ["https://notexample.com/post"]},
            {"query": "q3", "citations": ["https://example.com/"]},
        ]
        result = build_source_map("example.com", answers)

        by_domain = {entry["domain"]: entry for entry in result["top_domains"]}
        self.assertTrue(by_domain["blog.example.com"]["is_own_domain"])
        self.assertTrue(by_domain["example.com"]["is_own_domain"])
        self.assertFalse(by_domain["notexample.com"]["is_own_domain"])
        # 2 of the 3 answers cite an own-domain host (blog.example.com, example.com).
        self.assertAlmostEqual(result["own_domain_share"], 2 / 3, places=3)

    def test_unparseable_links_skipped_and_counted(self):
        answers = [
            {
                "query": "q1",
                "citations": [
                    "https://g2.com/page",
                    "not a url at all",
                    "",
                    "ftp://",
                ],
            },
        ]
        result = build_source_map("example.com", answers)

        self.assertEqual(result["unique_domains"], 1)
        self.assertEqual(result["top_domains"][0]["domain"], "g2.com")
        self.assertEqual(result["unparseable_links"], 3)

    def test_sorting_and_top_20_limit(self):
        answers = []
        for _ in range(3):
            answers.append({"query": "popular-q", "citations": ["https://popular.com/page"]})
        for index in range(1, 25):
            domain = f"d{index:02d}.com"
            answers.append({"query": f"q{index}", "citations": [f"https://{domain}/page"]})

        result = build_source_map("example.com", answers)

        self.assertEqual(result["unique_domains"], 25)
        self.assertEqual(len(result["top_domains"]), 20)
        self.assertEqual(result["top_domains"][0]["domain"], "popular.com")
        self.assertEqual(result["top_domains"][0]["answers_citing"], 3)
        # Tied at answers_citing == 1, so the rest is alphabetical: d01..d19.
        tied_domains = [entry["domain"] for entry in result["top_domains"][1:]]
        self.assertEqual(tied_domains, [f"d{i:02d}.com" for i in range(1, 20)])
        self.assertNotIn("d20.com", [entry["domain"] for entry in result["top_domains"]])

    def test_no_answers_returns_empty_stats(self):
        result = build_source_map("example.com", [])

        self.assertEqual(
            result,
            {
                "total_answers": 0,
                "unique_domains": 0,
                "unparseable_links": 0,
                "own_domain_share": 0.0,
                "top_domains": [],
            },
        )

    def test_deterministic_output(self):
        answers = [
            {"query": "q1", "citations": ["https://www.g2.com/a", "https://capterra.com/b"]},
            {"query": "q2", "citations": ["https://example.com/"]},
            {"query": "q3", "citations": []},
        ]
        first = build_source_map("example.com", answers)
        second = build_source_map("example.com", answers)

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
