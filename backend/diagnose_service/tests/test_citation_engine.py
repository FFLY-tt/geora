"""
Tests for citation_engine's multi-run mention-rate scoring.

Handles its own import path (backend/diagnose_service and backend/ on
sys.path) instead of requiring any change to existing code. Every
Perplexity and DeepSeek call is mocked: no API key is required, and
none of this ever touches the network, so it runs the same locally and
in CI (which has no keys configured at all).
"""

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

import engines.citation_engine as citation_engine  # noqa: E402
from shared.config import Settings  # noqa: E402

COMPANY_NAME = "ExampleCo"
DOMAIN = "exampleco.com"
CATEGORY = "browser side security and privacy compliance monitoring"
QUERIES = [
    "How do teams monitor third party scripts on checkout pages?",
    "What tools help meet PCI DSS requirements for client side code?",
    "How can a security team detect unauthorized data collection scripts?",
    "What is the best way to audit JavaScript running in a browser?",
    "How do companies stay compliant with privacy laws for web tracking?",
]

_FAKE_PLAIN_FINDING = ({"what_we_found": "stub", "what_it_costs_you": "", "fix_it_today": ""}, [])


def _canned_response(mentioned: bool) -> dict:
    if mentioned:
        return {
            "choices": [{"message": {"content": f"{COMPANY_NAME} is a strong option for this."}}],
            "citations": [f"https://{DOMAIN}/product"],
        }
    return {
        "choices": [{"message": {"content": "Some other vendor handles this well."}}],
        "citations": ["https://unrelated-example.org/page"],
    }


class _PerplexityStub:
    """
    Thread-safe stand-in for citation_engine._call_perplexity.

    For each query, consumes outcomes from a fixed list in whatever
    order the thread pool happens to call them in. Only the total count
    of hits/misses/failures per query ever affects mention_rate, never
    which specific run got which outcome, so consuming the list in
    arrival order instead of a fixed order is safe and still
    deterministic for the final result.
    """

    def __init__(self, patterns: dict[str, list[str]]):
        self._patterns = patterns
        self._lock = threading.Lock()
        self._next_index = {query: 0 for query in patterns}
        self.call_count = 0

    def __call__(self, query: str) -> dict:
        with self._lock:
            index = self._next_index[query]
            self._next_index[query] += 1
            self.call_count += 1

        outcome = self._patterns[query][index]
        if outcome == "fail":
            raise requests.ConnectionError("simulated Perplexity failure")
        return _canned_response(mentioned=(outcome == "hit"))


def _run_with_patterns(patterns: dict[str, list[str]]):
    """Run check_citation_visibility with every external call mocked, and return (result, stub)."""
    fake_settings = Settings(perplexity_api_key="test-key", deepseek_api_key=None)
    stub = _PerplexityStub(patterns)

    with patch.object(citation_engine, "settings", fake_settings), \
         patch.object(citation_engine, "get_cached_queries", return_value=(CATEGORY, QUERIES)), \
         patch.object(citation_engine, "_call_perplexity", side_effect=stub), \
         patch.object(citation_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
        result = citation_engine.check_citation_visibility(COMPANY_NAME, DOMAIN)

    return result, stub


class CitationEngineMultiRunTests(unittest.TestCase):
    def test_score_from_mixed_hit_patterns(self):
        # 3/3, 2/3, 1/3, 0/3, 0/3 -> average mention rate 0.4 -> score 40.
        patterns = {
            QUERIES[0]: ["hit", "hit", "hit"],
            QUERIES[1]: ["hit", "hit", "miss"],
            QUERIES[2]: ["hit", "miss", "miss"],
            QUERIES[3]: ["miss", "miss", "miss"],
            QUERIES[4]: ["miss", "miss", "miss"],
        }
        result, stub = _run_with_patterns(patterns)

        self.assertEqual(result.score, 40)
        self.assertEqual(stub.call_count, 15)

    def test_partial_run_failure_uses_only_successful_runs(self):
        patterns = {
            QUERIES[0]: ["hit", "fail", "miss"],  # 1 hit out of 2 successful runs -> 0.5
            QUERIES[1]: ["hit", "hit", "hit"],
            QUERIES[2]: ["hit", "hit", "hit"],
            QUERIES[3]: ["hit", "hit", "hit"],
            QUERIES[4]: ["hit", "hit", "hit"],
        }
        result, _ = _run_with_patterns(patterns)

        entry = next(e for e in result.details["per_query_results"] if e["query"] == QUERIES[0])
        self.assertEqual(entry["successful_runs"], 2)
        self.assertEqual(entry["failed_runs"], 1)
        self.assertAlmostEqual(entry["mention_rate"], 0.5)

    def test_question_with_all_runs_failed_excluded_from_average(self):
        patterns = {
            QUERIES[0]: ["fail", "fail", "fail"],
            QUERIES[1]: ["hit", "hit", "hit"],
            QUERIES[2]: ["hit", "hit", "hit"],
            QUERIES[3]: ["hit", "hit", "hit"],
            QUERIES[4]: ["hit", "hit", "hit"],
        }
        result, _ = _run_with_patterns(patterns)

        self.assertIn(QUERIES[0], result.details["fully_failed_queries"])
        self.assertEqual(result.details["queries_valid"], 4)
        # The 4 remaining valid queries are all 3/3 hits.
        self.assertEqual(result.score, 100)

    def test_all_calls_failed_returns_insufficient_data(self):
        patterns = {query: ["fail", "fail", "fail"] for query in QUERIES}
        result, stub = _run_with_patterns(patterns)

        self.assertEqual(result.score, -1)
        self.assertEqual(result.details["status"], "insufficient_data")
        self.assertEqual(stub.call_count, 15)

    def test_deterministic_output_for_same_mock_input(self):
        patterns = {
            QUERIES[0]: ["hit", "hit", "hit"],
            QUERIES[1]: ["hit", "hit", "miss"],
            QUERIES[2]: ["hit", "miss", "miss"],
            QUERIES[3]: ["miss", "miss", "miss"],
            QUERIES[4]: ["miss", "miss", "miss"],
        }
        first, _ = _run_with_patterns(patterns)
        second, _ = _run_with_patterns(patterns)

        self.assertEqual(first.score, second.score)
        self.assertEqual(first.details["average_mention_rate"], second.details["average_mention_rate"])

    def test_exactly_15_calls_made(self):
        patterns = {query: ["hit", "miss", "hit"] for query in QUERIES}
        _, stub = _run_with_patterns(patterns)

        self.assertEqual(stub.call_count, len(QUERIES) * citation_engine.RUNS_PER_QUERY)
        self.assertEqual(stub.call_count, 15)


if __name__ == "__main__":
    unittest.main()
