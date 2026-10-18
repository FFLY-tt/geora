"""
Tests for fact_engine's fixed-field, 3-run majority vote design, and for
the Wilson interval helper used by citation_visibility.

Fully offline: page fetches, Perplexity and DeepSeek are all mocked, and
the shared state directory is redirected to a temp dir per test.
"""

import json
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

import engines._state_store as state_store  # noqa: E402
import engines.citation_engine as citation_engine  # noqa: E402
import engines.fact_engine as fact_engine  # noqa: E402
from engines._stats import wilson_interval  # noqa: E402
from shared.config import Settings  # noqa: E402

DOMAIN = "acme.com"
OFFICIAL_TEXT = (
    "Acme is a CRM for small sales teams. Plans start at $29 per user per month. "
    "Try Acme free for 14 days. Integrates with Gmail, Outlook and Zapier. Cloud-based, no install needed."
)
_FAKE_SETTINGS = Settings(perplexity_api_key="p-key", deepseek_api_key="d-key")
_FAKE_PLAIN_FINDING = ({"what_we_found": "stub", "what_it_costs_you": "", "fix_it_today": ""}, [])
G2 = "https://www.g2.com/products/acme/reviews"


def _perplexity_reply(values: dict, citations=(G2,)) -> dict:
    """values: {field: (value, source_url)}; fields left out are null."""
    body = {name: {"value": None, "source_url": None} for name in fact_engine.FACT_FIELDS}
    for name, (value, source) in values.items():
        body[name] = {"value": value, "source_url": source}
    return {"choices": [{"message": {"content": json.dumps(body)}}], "citations": list(citations)}


def _comparison(verdicts: dict, quote="Plans start at $29 per user per month.") -> dict:
    """verdicts: {field: verdict}; every listed field gets an official value and a quote."""
    return {
        name: (
            {"official_value": "official", "official_quote": quote, "verdict": verdicts[name]}
            if name in verdicts
            else {"official_value": None, "official_quote": "", "verdict": "unknown"}
        )
        for name in fact_engine.FACT_FIELDS
    }


class _Sequence:
    """Thread-safe stand-in returning one prepared reply per call, in call order."""

    def __init__(self, replies):
        self._replies = list(replies)
        self._lock = threading.Lock()
        self.calls = 0

    def __call__(self, *args, **kwargs):
        with self._lock:
            reply = self._replies[self.calls]
            self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        return reply


class WilsonIntervalTests(unittest.TestCase):
    def test_3_of_15(self):
        low, high = wilson_interval(3, 15)
        self.assertAlmostEqual(low, 0.070, places=3)
        self.assertAlmostEqual(high, 0.452, places=3)

    def test_0_of_15_lower_bound_is_0(self):
        low, high = wilson_interval(0, 15)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.0)

    def test_15_of_15_upper_bound_is_1(self):
        low, high = wilson_interval(15, 15)
        self.assertEqual(high, 1.0)
        self.assertLess(low, 1.0)

    def test_total_0_does_not_raise(self):
        self.assertIsNone(wilson_interval(0, 0))

    def test_more_answers_give_a_narrower_interval(self):
        small = wilson_interval(3, 15)
        big = wilson_interval(30, 150)
        self.assertLess(big[1] - big[0], small[1] - small[0])


class CitationIntervalTests(unittest.TestCase):
    def test_citation_details_carry_mentions_total_and_interval(self):
        queries = [f"Question {i}?" for i in range(15)]
        question_set = (
            {"category_id": "c", "category_name": "C", "category_description": "d", "version": 1,
             "status": "frozen", "questions": queries, "source": "existing_company_assignment"},
            {},
        )
        hits = iter([True, False, False] * 5)  # 15 questions, each asked once
        lock = threading.Lock()

        def fake_perplexity(query):
            with lock:
                hit = next(hits)
            text = "Acme does this well." if hit else "Some other vendor does this."
            return {"choices": [{"message": {"content": text}}], "citations": []}

        with patch.object(citation_engine, "settings", _FAKE_SETTINGS), \
             patch.object(citation_engine, "_resolve_question_set", return_value=question_set), \
             patch.object(citation_engine, "_call_perplexity", side_effect=fake_perplexity), \
             patch.object(citation_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
            result = citation_engine.check_citation_visibility("Acme", DOMAIN)

        self.assertEqual((result.details["mentions"], result.details["total"]), (5, 15))
        low, high = wilson_interval(5, 15)
        self.assertEqual(result.details["ci_low"], round(low, 3))
        self.assertEqual(result.details["ci_high"], round(high, 3))


class VoteTests(unittest.TestCase):
    def test_two_conflicts_is_a_conflict(self):
        self.assertEqual(fact_engine.vote(["conflict", "conflict", "match"]), "conflict")

    def test_no_majority_is_unknown(self):
        self.assertEqual(fact_engine.vote(["conflict", "match", "unknown"]), "unknown")

    def test_two_matches_is_a_match(self):
        self.assertEqual(fact_engine.vote(["match", "match", "conflict"]), "match")

    def test_two_unknowns_is_unknown(self):
        self.assertEqual(fact_engine.vote(["unknown", "unknown", "conflict"]), "unknown")


class FactEngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._state = patch.object(state_store, "STATE_DIR", Path(self._tmp.name))
        self._state.start()

    def tearDown(self):
        self._state.stop()
        self._tmp.cleanup()

    def _run(self, perplexity_replies, comparison_replies, official_text=OFFICIAL_TEXT):
        perplexity = _Sequence(perplexity_replies)
        deepseek = _Sequence(comparison_replies)
        with patch.object(fact_engine, "settings", _FAKE_SETTINGS), \
             patch.object(fact_engine, "fetch_page_text", side_effect=lambda url: official_text if "pricing" not in url else ""), \
             patch.object(fact_engine, "_call_perplexity", side_effect=perplexity), \
             patch.object(fact_engine, "_call_deepseek_for_fact_comparison", side_effect=deepseek), \
             patch.object(fact_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
            result = fact_engine.check_fact_consistency("Acme", DOMAIN)
        return result, perplexity, deepseek

    def _field(self, result, name):
        return next(f for f in result.details["fields"] if f["field"] == name)

    def test_majority_vote_per_field_and_ladder_score(self):
        tp = _perplexity_reply({"starting_price": ("$49 per user per month", G2), "deployment": ("Cloud", G2)})
        runs = [
            _comparison({"starting_price": "conflict", "deployment": "match"}),
            _comparison({"starting_price": "conflict", "deployment": "match"}),
            _comparison({"starting_price": "match", "deployment": "conflict"}),
        ]
        result, perplexity, deepseek = self._run([tp, tp, tp], runs)

        self.assertEqual((perplexity.calls, deepseek.calls), (3, 3))
        self.assertEqual(self._field(result, "starting_price")["verdict"], "conflict")
        # Runs execute concurrently, so which run got which fake reply varies;
        # only the multiset of votes is fixed.
        self.assertEqual(sorted(self._field(result, "starting_price")["votes"]), ["conflict", "conflict", "match"])
        self.assertEqual(self._field(result, "deployment")["verdict"], "match")
        self.assertEqual(result.details["conflict_fields"], ["starting_price"])
        self.assertEqual(result.score, 60)  # 1 voted conflict
        field = self._field(result, "starting_price")
        self.assertEqual(field["third_party_source"], G2)
        self.assertEqual(field["official_quote"], "Plans start at $29 per user per month.")

    def test_official_quote_not_in_the_text_makes_the_field_unknown(self):
        tp = _perplexity_reply({"starting_price": ("$49", G2)})
        fake_quote = _comparison({"starting_price": "conflict"}, quote="Plans start at $9 per month.")
        result, _, _ = self._run([tp, tp, tp], [fake_quote] * 3)

        self.assertEqual(self._field(result, "starting_price")["votes"], ["unknown"] * 3)
        self.assertEqual(result.score, -1)
        reasons = {run["fields"]["starting_price"]["reason"] for run in result.details["runs"]}
        self.assertEqual(reasons, {"official quote not found word for word in the official text"})

    def test_third_party_value_from_the_companys_own_site_is_unknown(self):
        own = "https://acme.com/pricing"
        tp = _perplexity_reply({"starting_price": ("$49", own)}, citations=(own,))
        result, _, _ = self._run([tp] * 3, [_comparison({"starting_price": "conflict"})] * 3)
        self.assertEqual(self._field(result, "starting_price")["verdict"], "unknown")

    def test_third_party_source_not_among_citations_is_unknown(self):
        tp = _perplexity_reply({"starting_price": ("$49", "https://www.capterra.com/p/1/acme")}, citations=(G2,))
        result, _, _ = self._run([tp] * 3, [_comparison({"starting_price": "conflict"})] * 3)
        self.assertEqual(self._field(result, "starting_price")["verdict"], "unknown")

    def test_all_unknown_is_insufficient_third_party_information(self):
        tp = _perplexity_reply({})
        result, _, _ = self._run([tp] * 3, [_comparison({})] * 3)
        self.assertEqual(result.score, -1)
        self.assertIn("Not enough third-party information", result.details["reason"])

    def test_one_failed_run_still_votes_with_the_other_two(self):
        tp = _perplexity_reply({"deployment": ("Cloud", G2)})
        result, _, _ = self._run(
            [tp, requests.ConnectionError("boom"), tp],
            [_comparison({"deployment": "match"})] * 2,
        )
        self.assertEqual(self._field(result, "deployment")["verdict"], "match")
        self.assertEqual(result.score, 95)

    def test_cache_hit_returns_the_voted_result_without_api_calls(self):
        tp = _perplexity_reply({"starting_price": ("$49", G2)})
        first, _, _ = self._run([tp] * 3, [_comparison({"starting_price": "conflict"})] * 3)
        self.assertEqual(first.details["cache_status"], "fresh_judgment")

        second, perplexity, deepseek = self._run([RuntimeError("no call")] * 3, [RuntimeError("no call")] * 3)
        self.assertEqual((perplexity.calls, deepseek.calls), (0, 0))
        self.assertEqual(second.details["cache_status"], "cache_hit")
        self.assertEqual(second.score, first.score)
        self.assertEqual(second.details["fields"], first.details["fields"])

    def test_cache_key_includes_the_field_list_version(self):
        with patch.object(fact_engine, "settings", _FAKE_SETTINGS):
            key_v1 = fact_engine._cache_key("Acme", DOMAIN, OFFICIAL_TEXT)
            with patch.object(fact_engine, "FIELDS_VERSION", "v3"):
                key_v2 = fact_engine._cache_key("Acme", DOMAIN, OFFICIAL_TEXT)
        self.assertNotEqual(key_v1, key_v2)

    def test_key_integrations_is_not_a_compared_field(self):
        self.assertNotIn("key_integrations", fact_engine.FACT_FIELDS)
        self.assertNotIn("key_integrations", fact_engine.FACT_FIELD_LABELS)
        self.assertEqual(
            list(fact_engine.FACT_FIELDS),
            ["starting_price", "free_plan_or_trial", "product_category", "target_customer", "deployment"],
        )
        # the Perplexity question and the comparison prompt are built from the field list
        self.assertNotIn("integrations", fact_engine._build_third_party_query("Acme", DOMAIN))
        self.assertNotIn("key_integrations", fact_engine._COMPARISON_SYSTEM_PROMPT)

    def test_an_old_cached_result_with_the_old_field_list_is_not_reused(self):
        with patch.object(fact_engine, "settings", _FAKE_SETTINGS):
            current = fact_engine._cache_key("Acme", DOMAIN, OFFICIAL_TEXT)
            with patch.object(fact_engine, "FIELDS_VERSION", "v1"), \
                 patch.object(fact_engine, "FACT_FIELDS", {**fact_engine.FACT_FIELDS, "key_integrations": "the main integrations it names"}):
                old = fact_engine._cache_key("Acme", DOMAIN, OFFICIAL_TEXT)
        self.assertNotEqual(current, old)

    def test_versions_were_bumped(self):
        self.assertEqual(fact_engine.METHOD_VERSION, "v3")
        self.assertEqual(fact_engine.FIELDS_VERSION, "v2")
        self.assertTrue(fact_engine.THIRD_PARTY_LOOKUP_VERSION.startswith("v2"))
        self.assertTrue(fact_engine.COMPARISON_PROMPT_VERSION.startswith("v2"))


if __name__ == "__main__":
    unittest.main()
