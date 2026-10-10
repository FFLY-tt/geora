"""
Tests for evidence_engine's multi-run median scoring.

Every DeepSeek call and page fetch is mocked: no API key is required,
and none of this ever touches the network, so it runs the same locally
and in CI.
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

import engines.evidence_engine as evidence_engine  # noqa: E402
from shared.config import Settings  # noqa: E402

DOMAIN = "example.com"
PAGE_TEXT = "ExampleCo cut onboarding time by 42% for 1,200 customers, per our Q3 case study."

_FAKE_PLAIN_FINDING = ({"what_we_found": "stub", "what_it_costs_you": "", "fix_it_today": ""}, [])
_FAKE_SETTINGS = Settings(perplexity_api_key=None, deepseek_api_key="test-key")


def _judgment(score: int, reasoning: str = "Some reasoning.") -> dict:
    return {"score": score, "reasoning": reasoning}


def _run_with(get_clean_judgment_side_effect):
    with patch.object(evidence_engine, "settings", _FAKE_SETTINGS), \
         patch.object(evidence_engine, "fetch_page_text", return_value=PAGE_TEXT), \
         patch.object(evidence_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING), \
         patch.object(
             evidence_engine, "_get_clean_evidence_judgment", side_effect=get_clean_judgment_side_effect
         ):
        return evidence_engine.check_evidence_density(DOMAIN)


class EvidenceEngineMedianScoringTests(unittest.TestCase):
    def test_three_scores_use_median_not_average(self):
        # [42, 55, 62] -> median 55 (average would be ~53).
        scores = iter([42, 55, 62])

        def side_effect(page_text):
            return _judgment(next(scores)), [{"attempt": 1, "issues": []}]

        result = _run_with(side_effect)

        self.assertEqual(result.score, 55)
        self.assertEqual(result.details["median_score"], 55)
        self.assertEqual(result.details["status"], "ok")
        self.assertEqual(len(result.details["runs"]), 3)
        self.assertEqual(sorted(run["score"] for run in result.details["runs"]), [42, 55, 62])

    def test_one_failed_run_uses_median_of_remaining_two(self):
        call_count = {"n": 0}

        def side_effect(page_text):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise requests.ConnectionError("simulated DeepSeek failure")
            return _judgment(40 if call_count["n"] == 2 else 60), [{"attempt": 1, "issues": []}]

        result = _run_with(side_effect)

        self.assertEqual(result.details["status"], "ok")
        self.assertEqual(result.score, 50)
        successful = [run for run in result.details["runs"] if run["status"] == "ok"]
        failed = [run for run in result.details["runs"] if run["status"] == "error"]
        self.assertEqual(len(successful), 2)
        self.assertEqual(len(failed), 1)

    def test_all_runs_failed_returns_insufficient_data(self):
        def side_effect(page_text):
            raise requests.ConnectionError("simulated DeepSeek failure")

        result = _run_with(side_effect)

        self.assertEqual(result.score, -1)
        self.assertEqual(result.details["status"], "insufficient_data")
        self.assertEqual(len(result.details["runs"]), 3)
        self.assertTrue(all(run["status"] == "error" for run in result.details["runs"]))

    def test_deterministic_output_for_same_mock_input(self):
        def side_effect(page_text):
            return _judgment(50), [{"attempt": 1, "issues": []}]

        first = _run_with(side_effect)
        second = _run_with(side_effect)

        self.assertEqual(first.score, second.score)
        self.assertEqual(first.score, 50)


if __name__ == "__main__":
    unittest.main()
