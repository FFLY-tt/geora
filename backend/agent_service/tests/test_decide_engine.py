"""Tests for decide_engine.decide(). Pure function tests, no HTTP involved."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from decide_engine import decide


def _dim(dimension: str, score: int, details: dict | None = None) -> dict:
    return {"dimension": dimension, "score": score, "details": details or {}}


class DecideEngineTests(unittest.TestCase):
    def test_case_a_ranking_and_priority_values(self):
        dimensions = [
            _dim("topic_relevance", 55),
            _dim("evidence_density", 40),
            _dim("content_recency", 25),
            _dim("fact_consistency", 45),
            _dim("citation_visibility", 30),
        ]
        result = decide("example.com", "Example Inc.", dimensions)

        self.assertEqual(result["outcome"], {"dimension": "citation_visibility", "score": 30})
        self.assertEqual(
            [(item["dimension"], item["priority"]) for item in result["all_ranked"]],
            [
                ("content_recency", 51.0),
                ("fact_consistency", 27.5),
                ("evidence_density", 18.0),
                ("topic_relevance", 9.0),
            ],
        )
        self.assertEqual([item["rank"] for item in result["all_ranked"]], [1, 2, 3, 4])
        self.assertEqual(result["this_week"], result["all_ranked"][:3])

    def test_case_b_ranking_and_priority_values(self):
        dimensions = [
            _dim("topic_relevance", 30),
            _dim("evidence_density", 70),
            _dim("content_recency", 85),
            _dim("fact_consistency", 40),
            _dim("citation_visibility", 35),
        ]
        result = decide("example.com", "Example Inc.", dimensions)

        self.assertEqual(
            [(item["dimension"], item["priority"]) for item in result["all_ranked"]],
            [
                ("fact_consistency", 30.0),
                ("topic_relevance", 14.0),
                ("content_recency", 10.2),
                ("evidence_density", 9.0),
            ],
        )

    def test_missing_score_goes_to_needs_data_and_is_never_ranked(self):
        dimensions = [
            _dim("content_recency", -1, {"reason": "Failed to fetch homepage."}),
            _dim("fact_consistency", 45),
            _dim("evidence_density", 40),
            _dim("topic_relevance", 55),
            _dim("citation_visibility", 30),
        ]
        result = decide("example.com", "Example Inc.", dimensions)

        self.assertEqual(result["needs_data"], [{"dimension": "content_recency", "reason": "Failed to fetch homepage."}])
        ranked_dimensions = [item["dimension"] for item in result["all_ranked"]]
        self.assertNotIn("content_recency", ranked_dimensions)
        self.assertEqual(len(result["all_ranked"]), 3)

    def test_citation_visibility_never_appears_in_all_ranked(self):
        dimensions = [
            _dim("citation_visibility", 60),
            _dim("content_recency", 50),
        ]
        result = decide("example.com", "Example Inc.", dimensions)

        ranked_dimensions = [item["dimension"] for item in result["all_ranked"]]
        self.assertNotIn("citation_visibility", ranked_dimensions)

    def test_unrecognized_dimension_is_ignored_and_warned_about(self):
        dimensions = [
            _dim("content_recency", 50),
            _dim("some_future_dimension", 20),
        ]
        result = decide("example.com", "Example Inc.", dimensions)

        ranked_dimensions = [item["dimension"] for item in result["all_ranked"]]
        self.assertNotIn("some_future_dimension", ranked_dimensions)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("some_future_dimension", result["warnings"][0])

    def test_deterministic_output_for_same_input(self):
        dimensions = [
            _dim("topic_relevance", 55),
            _dim("evidence_density", 40),
            _dim("content_recency", 25),
            _dim("fact_consistency", 45),
            _dim("citation_visibility", 30),
        ]
        first = decide("example.com", "Example Inc.", dimensions)
        second = decide("example.com", "Example Inc.", dimensions)
        self.assertEqual(first, second)

    def test_perfect_score_stays_in_all_ranked_but_not_this_week(self):
        dimensions = [
            _dim("content_recency", 100),
            _dim("fact_consistency", 45),
            _dim("evidence_density", 40),
            _dim("topic_relevance", 55),
        ]
        result = decide("example.com", "Example Inc.", dimensions)

        all_ranked_dimensions = [item["dimension"] for item in result["all_ranked"]]
        this_week_dimensions = [item["dimension"] for item in result["this_week"]]

        self.assertIn("content_recency", all_ranked_dimensions)
        self.assertNotIn("content_recency", this_week_dimensions)
        content_recency_item = next(item for item in result["all_ranked"] if item["dimension"] == "content_recency")
        self.assertEqual(content_recency_item["priority"], 0.0)

    def test_empty_dimensions_returns_empty_lists_without_error(self):
        result = decide("example.com", "Example Inc.", [])

        self.assertIsNone(result["outcome"])
        self.assertEqual(result["this_week"], [])
        self.assertEqual(result["all_ranked"], [])
        self.assertEqual(result["needs_data"], [])
        self.assertEqual(result["warnings"], [])


if __name__ == "__main__":
    unittest.main()
