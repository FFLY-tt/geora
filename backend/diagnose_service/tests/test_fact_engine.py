"""
Tests for fact_engine's multi-run median scoring.

Every DeepSeek/Perplexity call and page fetch is mocked: no API key is
required, and none of this ever touches the network, so it runs the
same locally and in CI.
"""

import statistics
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

_THIS_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _THIS_DIR.parent  # backend/diagnose_service
_BACKEND_DIR = _SERVICE_DIR.parent  # backend/

sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))

import engines.fact_engine as fact_engine  # noqa: E402
from shared.config import Settings  # noqa: E402

COMPANY_NAME = "Acme"
DOMAIN = "acme.com"
FIELDS = ["pricing", "supported regions", "core feature claims"]

_FAKE_PLAIN_FINDING = ({"what_we_found": "stub", "what_it_costs_you": "", "fix_it_today": ""}, [])
_FAKE_SETTINGS = Settings(perplexity_api_key="test-key", deepseek_api_key="test-key")
_PERPLEXITY_RESPONSE = {
    "choices": [{"message": {"content": "G2 and Capterra say some things about Acme."}}],
    "citations": ["https://www.g2.com/products/acme/reviews"],
}


def _conflict(field: str = "pricing") -> dict:
    return {"field": field, "official_says": "official value", "third_party_says": "third-party value"}


class _ComparisonStub:
    """
    Thread-safe stand-in for fact_engine._get_clean_fact_comparison.

    Consumes outcomes from a fixed list in whatever order the thread
    pool happens to call them in. Only the multiset of outcomes ever
    affects the median, never which specific run got which outcome, so
    consuming the list in arrival order instead of a fixed order is
    safe and still deterministic for the final result (same pattern as
    citation_engine's test _PerplexityStub).
    """

    def __init__(self, outcomes: list):
        self._outcomes = outcomes
        self._lock = threading.Lock()
        self._next_index = 0

    def __call__(self, company_name, official_text, third_party_text):
        with self._lock:
            index = self._next_index
            self._next_index += 1

        outcome = self._outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome

        compared_fields, conflicts = outcome
        return {"compared_fields": compared_fields, "conflicts": conflicts}, [{"attempt": 1, "issues": []}]


def _run_with(comparison_outcomes: list):
    stub = _ComparisonStub(comparison_outcomes)

    with patch.object(fact_engine, "settings", _FAKE_SETTINGS), \
         patch.object(fact_engine, "_call_perplexity", return_value=_PERPLEXITY_RESPONSE), \
         patch.object(fact_engine, "_fetch_official_text", return_value=("Official homepage text.", [f"https://{DOMAIN}"])), \
         patch.object(fact_engine, "_get_clean_fact_comparison", side_effect=stub), \
         patch.object(fact_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
        result = fact_engine.check_fact_consistency(COMPANY_NAME, DOMAIN)

    return result, stub


class FactEngineMedianScoringTests(unittest.TestCase):
    def test_three_different_conflict_counts_use_median_score(self):
        # 0 conflicts -> 95, 1 conflict -> 60, 2 conflicts -> 35. Median of
        # [95, 60, 35] is 60, not an average (which would be ~63.3).
        outcomes = [
            (FIELDS, []),
            (FIELDS, [_conflict("pricing")]),
            (FIELDS, [_conflict("pricing"), _conflict("supported regions")]),
        ]

        result, _stub = _run_with(outcomes)

        self.assertEqual(result.details["status"], "ok")
        self.assertEqual(result.score, 60)
        self.assertEqual(result.details["median_score"], 60)
        self.assertEqual(len(result.details["runs"]), 3)
        self.assertEqual(sorted(r["score"] for r in result.details["runs"]), [35, 60, 95])

    def test_one_failed_run_uses_median_of_remaining_two(self):
        outcomes = [
            requests.ConnectionError("simulated DeepSeek failure"),
            (FIELDS, []),  # score 95
            (FIELDS, [_conflict("pricing")]),  # score 60
        ]

        result, _stub = _run_with(outcomes)

        expected_score = round(statistics.median([95, 60]))
        self.assertEqual(result.details["status"], "ok")
        self.assertEqual(result.score, expected_score)
        successful = [r for r in result.details["runs"] if r["status"] == "ok"]
        failed = [r for r in result.details["runs"] if r["status"] == "error"]
        self.assertEqual(len(successful), 2)
        self.assertEqual(len(failed), 1)

    def test_all_runs_failed_returns_insufficient_data(self):
        outcomes = [requests.ConnectionError("boom")] * 3

        result, _stub = _run_with(outcomes)

        self.assertEqual(result.score, -1)
        self.assertEqual(result.details["status"], "insufficient_data")
        self.assertIn("all 3 runs", result.details["reason"])
        self.assertEqual(len(result.details["runs"]), 3)

    def test_all_runs_find_nothing_comparable_returns_insufficient_data(self):
        outcomes = [([], [])] * 3

        result, _stub = _run_with(outcomes)

        self.assertEqual(result.score, -1)
        self.assertEqual(result.details["status"], "insufficient_data")
        self.assertIn("No comparable fields", result.details["reason"])

    def test_deterministic_output_for_same_mock_input(self):
        outcomes = [(FIELDS, []), (FIELDS, []), (FIELDS, [])]

        first, _ = _run_with(outcomes)
        second, _ = _run_with(outcomes)

        self.assertEqual(first.score, second.score)
        self.assertEqual(first.score, 95)
        self.assertEqual(second.score, 95)


if __name__ == "__main__":
    unittest.main()
