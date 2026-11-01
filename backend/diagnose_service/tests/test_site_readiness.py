"""Tests for site_readiness.py: the score, levels and closing sentence of "Is your website ready for AI?"."""

import sys
import unittest
from pathlib import Path

_SERVICE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVICE_DIR.parent))
sys.path.insert(0, str(_SERVICE_DIR))

import site_readiness  # noqa: E402

DOMAIN = "me.com"


def dims(topic, evidence, recency, technical, own_site_answers=0, answers=15):
    runs = [
        {"status": "ok", "all_citations": ["https://www.me.com/pricing"] if i < own_site_answers else ["https://other.com/x"]}
        for i in range(answers)
    ]
    citation = {"dimension": "citation_visibility", "score": 10, "details": {"per_query_results": [{"runs": runs}]}}
    others = [
        {"dimension": "topic_relevance", "score": topic},
        {"dimension": "evidence_density", "score": evidence},
        {"dimension": "content_recency", "score": recency},
        {"dimension": "technical_readability", "score": technical},
        {"dimension": "fact_consistency", "score": 5},
    ]
    return [citation] + others


class SiteReadinessTests(unittest.TestCase):
    def test_the_total_is_the_average_rounded_half_up(self):
        self.assertEqual(site_readiness.build_site_readiness(dims(80, 60, 70, 70), DOMAIN)["total"], 70)  # 70.0
        self.assertEqual(site_readiness.build_site_readiness(dims(80, 60, 70, 71), DOMAIN)["total"], 70)  # 70.25
        self.assertEqual(site_readiness.build_site_readiness(dims(80, 60, 70, 72), DOMAIN)["total"], 71)  # 70.5
        self.assertEqual(site_readiness.build_site_readiness(dims(10, 11, 10, 10), DOMAIN)["total"], 10)  # 10.25

    def test_fact_consistency_is_not_part_of_it(self):
        block = site_readiness.build_site_readiness(dims(100, 100, 100, 100), DOMAIN)
        self.assertEqual(block["total"], 100)
        self.assertEqual([item["key"] for item in block["items"]], ["topic_relevance", "evidence_density", "content_recency", "technical_readability"])
        self.assertEqual(
            [item["name"] for item in block["items"]],
            ["Answers customer questions", "Has real numbers and proof", "Up to date", "Easy for AI to read"],
        )

    def test_a_check_without_a_result_is_left_out(self):
        block = site_readiness.build_site_readiness(dims(80, -1, 60, 70), DOMAIN)
        self.assertEqual(block["total"], 70)  # (80 + 60 + 70) / 3
        evidence = next(item for item in block["items"] if item["key"] == "evidence_density")
        self.assertEqual((evidence["score"], evidence["level"]), (None, None))

    def test_the_levels_change_at_70_and_40(self):
        for score, level in [(100, "Good"), (70, "Good"), (69, "Needs work"), (40, "Needs work"), (39, "Weak"), (0, "Weak")]:
            self.assertEqual(site_readiness.level_for(score), level, score)
        self.assertIsNone(site_readiness.level_for(None))
        block = site_readiness.build_site_readiness(dims(70, 69, 40, 39), DOMAIN)
        self.assertEqual([item["level"] for item in block["items"]], ["Good", "Needs work", "Needs work", "Weak"])

    def test_good_and_ai_never_used_the_website(self):
        block = site_readiness.build_site_readiness(dims(90, 80, 70, 80, own_site_answers=0), DOMAIN)
        self.assertEqual(block["sentence"], "Your website is in good shape. AI just isn't reading it for these questions yet.")
        self.assertEqual(block["fix_first"], [])

    def test_good_and_ai_used_the_website(self):
        block = site_readiness.build_site_readiness(dims(90, 80, 70, 80, own_site_answers=3), DOMAIN)
        self.assertEqual(block["sentence"], "Your website is in good shape. AI used it in 3 of 15 answers.")

    def test_below_70_the_sentence_points_at_the_lowest_one_or_two(self):
        block = site_readiness.build_site_readiness(dims(80, 20, 55, 90), DOMAIN)  # total 61
        self.assertEqual(block["total"], 61)
        self.assertEqual(block["sentence"], "Start by fixing these:")
        self.assertEqual(block["fix_first"], ["Has real numbers and proof", "Up to date"])

    def test_only_one_name_when_the_second_lowest_is_good(self):
        block = site_readiness.build_site_readiness(dims(70, 10, 90, 90), DOMAIN)  # total 65
        self.assertEqual(block["fix_first"], ["Has real numbers and proof"])

    def test_a_tie_for_the_lowest_goes_to_the_check_that_comes_first(self):
        block = site_readiness.build_site_readiness(dims(30, 30, 30, 80), DOMAIN)
        self.assertEqual(block["fix_first"], ["Answers customer questions", "Has real numbers and proof"])

    def test_no_result_at_all_means_no_score(self):
        block = site_readiness.build_site_readiness(dims(-1, -1, -1, -1), DOMAIN)
        self.assertIsNone(block["total"])
        self.assertIsNone(block["level"])
        self.assertEqual(block["sentence"], "We could not read your website.")
        self.assertEqual(block["fix_first"], [])
        self.assertEqual(site_readiness.build_site_readiness([], DOMAIN)["sentence"], "We could not read your website.")

    def test_only_the_company_own_domain_counts_as_its_website(self):
        runs = [{"status": "ok", "all_citations": ["https://notme.com/x", "https://me.com.evil.org/y"]}]
        self.assertEqual(site_readiness.answers_using_own_site({"per_query_results": [{"runs": runs}]}, DOMAIN), (0, 1))
        runs = [{"status": "ok", "all_citations": ["https://docs.me.com/x"]}, {"status": "error"}]
        self.assertEqual(site_readiness.answers_using_own_site({"per_query_results": [{"runs": runs}]}, DOMAIN), (1, 1))

    def test_same_input_twice_gives_identical_results(self):
        self.assertEqual(
            site_readiness.build_site_readiness(dims(80, 20, 55, 90), DOMAIN), site_readiness.build_site_readiness(dims(80, 20, 55, 90), DOMAIN)
        )


if __name__ == "__main__":
    unittest.main()
