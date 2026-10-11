"""
Tests for evidence_engine's evidence-listing + code-scoring + judgment
cache design.

Every DeepSeek call and page fetch is mocked: no API key is required,
and none of this ever touches the network, so it runs the same
locally and in CI. The judgment cache is redirected to a temp file per
test so tests never read or write the real on-disk cache and never
leak state between tests.
"""

import sys
import tempfile
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

import engines.evidence_engine as evidence_engine  # noqa: E402
import engines._judgment_cache as judgment_cache  # noqa: E402
import engines._state_store as state_store  # noqa: E402
from shared.config import Settings  # noqa: E402

DOMAIN = "example.com"
PAGE_TEXT = (
    "ExampleCo cut onboarding time by 42% for 1,200 customers, per our Q3 case study. "
    '"We saved $100,000 in the first year," said Dana Example, Founder at Acme Corp. '
    "ExampleCo is SOC 2 certified and rated 4.7 on G2."
)

_FAKE_PLAIN_FINDING = ({"what_we_found": "stub", "what_it_costs_you": "", "fix_it_today": ""}, [])
_FAKE_SETTINGS = Settings(perplexity_api_key=None, deepseek_api_key="test-key")


class _ListingStub:
    """
    Thread-safe stand-in for evidence_engine._call_deepseek_for_evidence_listing.
    Consumes outcomes from a fixed list in whatever order the thread
    pool happens to call them in (same pattern as citation_engine's
    test _PerplexityStub) — only the multiset of outcomes matters for
    the aggregated union, never which specific run got which outcome.
    """

    def __init__(self, outcomes: list):
        self._outcomes = outcomes
        self._lock = threading.Lock()
        self._next_index = 0
        self.call_count = 0

    def __call__(self, page_text):
        with self._lock:
            index = self._next_index
            self._next_index += 1
            self.call_count += 1

        outcome = self._outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class EvidenceEngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp_dir.name)
        self._cache_patches = [
            patch.object(state_store, "STATE_DIR", tmp_path),
        ]
        for p in self._cache_patches:
            p.start()

    def tearDown(self):
        for p in self._cache_patches:
            p.stop()
        self._tmp_dir.cleanup()

    def _run_with(self, listing_outcomes):
        stub = _ListingStub(listing_outcomes)
        with patch.object(evidence_engine, "settings", _FAKE_SETTINGS), \
             patch.object(evidence_engine, "fetch_page_text", return_value=PAGE_TEXT), \
             patch.object(evidence_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING), \
             patch.object(evidence_engine, "_call_deepseek_for_evidence_listing", side_effect=stub):
            result = evidence_engine.check_evidence_density(DOMAIN)
        return result, stub

    def test_verified_items_are_scored_by_code_not_llm(self):
        # 1 number, 1 named customer, 1 attributed quote, 0 certifications
        # found identically on all 3 runs -> union has 1 of each ->
        # 15+15+15+0 = 45 by the fixed scoring rule.
        outcome = [
            {"category": "number_or_statistic", "quote": "cut onboarding time by 42% for 1,200 customers"},
            {"category": "named_customer_or_case_study", "quote": "Dana Example, Founder at Acme Corp"},
            {"category": "attributed_quote", "quote": "We saved $100,000 in the first year"},
        ]
        result, stub = self._run_with([outcome, outcome, outcome])

        self.assertEqual(result.details["status"], "ok")
        self.assertEqual(stub.call_count, 3)
        self.assertEqual(result.details["counts_by_category"]["number_or_statistic"], 1)
        self.assertEqual(result.details["counts_by_category"]["named_customer_or_case_study"], 1)
        self.assertEqual(result.details["counts_by_category"]["attributed_quote"], 1)
        self.assertEqual(result.details["counts_by_category"]["third_party_source_or_certification"], 0)
        self.assertEqual(result.score, 15 + 15 + 15 + 0)
        self.assertEqual(result.details["cache_status"], "fresh_judgment")

    def test_three_or_more_items_in_a_category_earns_full_marks(self):
        outcome = [
            {"category": "number_or_statistic", "quote": "cut onboarding time by 42% for 1,200 customers"},
            {"category": "number_or_statistic", "quote": "rated 4.7 on G2"},
            {"category": "number_or_statistic", "quote": "SOC 2 certified"},
        ]
        result, _stub = self._run_with([outcome, outcome, outcome])

        self.assertEqual(result.details["counts_by_category"]["number_or_statistic"], 3)
        self.assertEqual(result.details["points_by_category"]["number_or_statistic"], evidence_engine.MAX_POINTS_PER_CATEGORY)

    def test_fabricated_quote_not_found_in_page_is_dropped(self):
        outcome = [
            {"category": "number_or_statistic", "quote": "This sentence does not appear on the page at all."},
        ]
        result, _stub = self._run_with([outcome, outcome, outcome])

        self.assertEqual(result.details["counts_by_category"]["number_or_statistic"], 0)
        self.assertEqual(result.score, 0)
        dropped = [d for run in result.details["runs"] for d in run["dropped_items"]]
        self.assertEqual(len(dropped), 3)

    def test_duplicate_item_across_runs_counted_once(self):
        outcome = [
            {"category": "number_or_statistic", "quote": "cut onboarding time by 42% for 1,200 customers"},
        ]
        # Same real item found independently by all 3 runs -> union
        # dedupes it to 1, not 3.
        result, _stub = self._run_with([outcome, outcome, outcome])

        self.assertEqual(result.details["counts_by_category"]["number_or_statistic"], 1)

    def test_unrecognized_category_is_dropped(self):
        outcome = [
            {"category": "not_a_real_category", "quote": "cut onboarding time by 42% for 1,200 customers"},
        ]
        result, _stub = self._run_with([outcome, outcome, outcome])

        self.assertEqual(sum(result.details["counts_by_category"].values()), 0)

    def test_second_call_with_identical_page_text_hits_cache_no_llm_call(self):
        outcome = [
            {"category": "number_or_statistic", "quote": "cut onboarding time by 42% for 1,200 customers"},
        ]
        first, stub1 = self._run_with([outcome, outcome, outcome])
        self.assertEqual(stub1.call_count, 3)
        self.assertEqual(first.details["cache_status"], "fresh_judgment")

        second, stub2 = self._run_with([RuntimeError("should never be called")] * 3)

        self.assertEqual(stub2.call_count, 0)
        self.assertEqual(second.details["cache_status"], "cache_hit")
        self.assertEqual(second.score, first.score)
        self.assertEqual(second.details["items"], first.details["items"])

    def test_different_page_text_does_not_hit_cache(self):
        outcome = [
            {"category": "number_or_statistic", "quote": "cut onboarding time by 42% for 1,200 customers"},
        ]
        self._run_with([outcome, outcome, outcome])

        stub = _ListingStub([outcome, outcome, outcome])
        with patch.object(evidence_engine, "settings", _FAKE_SETTINGS), \
             patch.object(evidence_engine, "fetch_page_text", return_value=PAGE_TEXT + " One more sentence."), \
             patch.object(evidence_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING), \
             patch.object(evidence_engine, "_call_deepseek_for_evidence_listing", side_effect=stub):
            result = evidence_engine.check_evidence_density(DOMAIN)

        self.assertEqual(stub.call_count, 3)
        self.assertEqual(result.details["cache_status"], "fresh_judgment")

    def test_all_runs_failed_returns_insufficient_data_and_is_not_cached(self):
        error = requests.ConnectionError("boom")
        result, stub = self._run_with([error, error, error])

        self.assertEqual(result.score, -1)
        self.assertEqual(result.details["status"], "insufficient_data")
        self.assertEqual(stub.call_count, 3)

        # A second attempt (even with the exact same page text) must
        # try again rather than serving a cached failure.
        stub2 = _ListingStub([error, error, error])
        with patch.object(evidence_engine, "settings", _FAKE_SETTINGS), \
             patch.object(evidence_engine, "fetch_page_text", return_value=PAGE_TEXT), \
             patch.object(evidence_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING), \
             patch.object(evidence_engine, "_call_deepseek_for_evidence_listing", side_effect=stub2):
            evidence_engine.check_evidence_density(DOMAIN)
        self.assertEqual(stub2.call_count, 3)

if __name__ == "__main__":
    unittest.main()
