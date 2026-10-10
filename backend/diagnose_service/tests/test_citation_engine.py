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


class CompanyNameMatchingTests(unittest.TestCase):
    """_execute_single_run's whole-word name matching and strict domain matching for NON-common-word names, tested directly (no HTTP, just _call_perplexity mocked)."""

    def _run_with_answer(self, answer_text: str, citations: list[str], domain: str, company_name: str) -> dict:
        with patch.object(
            citation_engine,
            "_call_perplexity",
            return_value={"choices": [{"message": {"content": answer_text}}], "citations": citations},
        ):
            return citation_engine._execute_single_run("some query", domain, company_name, CATEGORY, name_is_common_word=False)

    def test_non_common_word_name_match_alone_is_enough(self):
        result = self._run_with_answer(
            "Feroot Security is a strong option for this.",
            ["https://unrelated.com"],
            "feroot.com",
            "Feroot Security",
        )
        self.assertEqual(result["mention_rule"], "whole_word")
        self.assertTrue(result["is_hit"])

    def test_name_glued_to_other_letters_is_not_a_whole_word_match(self):
        result = self._run_with_answer(
            "FerootSecurityPlus helps with this, unrelated to the real brand.",
            [],
            "feroot.com",
            "Feroot Security",
        )
        self.assertFalse(result["is_hit"])

    def test_notdomain_does_not_match_citation(self):
        result = self._run_with_answer(
            "Some generic answer.",
            ["https://notferoot.com/page"],
            "feroot.com",
            "Feroot Security",
        )
        self.assertEqual(result["matched_citations"], [])
        self.assertFalse(result["is_hit"])

    def test_subdomain_matches_citation(self):
        result = self._run_with_answer(
            "Some generic answer.",
            ["https://app.feroot.com/page"],
            "feroot.com",
            "Feroot Security",
        )
        self.assertEqual(result["matched_citations"], ["https://app.feroot.com/page"])
        self.assertTrue(result["is_hit"])

    def test_non_common_word_name_never_calls_the_llm(self):
        with patch.object(citation_engine, "_call_deepseek_for_common_word_disambiguation") as mock_llm:
            self._run_with_answer(
                "Feroot Security is a strong option for this.",
                [],
                "feroot.com",
                "Feroot Security",
            )
        mock_llm.assert_not_called()

    def test_is_common_word_company_name(self):
        self.assertTrue(citation_engine._is_common_word_company_name("Close"))
        self.assertTrue(citation_engine._is_common_word_company_name("  Linear  "))
        self.assertTrue(citation_engine._is_common_word_company_name("ZOOM"))
        self.assertFalse(citation_engine._is_common_word_company_name("Feroot Security"))
        self.assertFalse(citation_engine._is_common_word_company_name("ExampleCo"))


class CommonWordDisambiguationTests(unittest.TestCase):
    """_execute_single_run's LLM-backed disambiguation path for common-word company names (Close, Linear, ...)."""

    def _run_with_answer(self, answer_text: str, citations: list[str], disambiguation_result=None, disambiguation_error=None) -> tuple[dict, "unittest.mock.MagicMock"]:
        patches = [
            patch.object(
                citation_engine,
                "_call_perplexity",
                return_value={"choices": [{"message": {"content": answer_text}}], "citations": citations},
            ),
        ]
        if disambiguation_error is not None:
            llm_patch = patch.object(citation_engine, "_call_deepseek_for_common_word_disambiguation", side_effect=disambiguation_error)
        else:
            llm_patch = patch.object(citation_engine, "_call_deepseek_for_common_word_disambiguation", return_value=disambiguation_result)
        patches.append(llm_patch)

        with patches[0], patches[1] as mock_llm:
            result = citation_engine._execute_single_run("some query", "close.com", "Close", CATEGORY, name_is_common_word=True)
        return result, mock_llm

    def test_close_the_deal_llm_says_no_is_not_a_hit(self):
        result, mock_llm = self._run_with_answer(
            "You should close the deal quickly to win this customer.",
            ["https://unrelated.com"],
            disambiguation_result={"refers_to_company": False, "evidence_sentence": ""},
        )
        mock_llm.assert_called_once()
        self.assertEqual(result["mention_rule"], "llm_disambiguation")
        self.assertEqual(result["mention_classification"], "no_mention")
        self.assertFalse(result["is_hit"])

    def test_close_is_a_strong_fit_llm_says_yes_with_real_evidence_is_a_hit(self):
        answer_text = "Close is a strong fit for small sales teams. It combines calling, texting, and email."
        result, mock_llm = self._run_with_answer(
            answer_text,
            [],
            disambiguation_result={
                "refers_to_company": True,
                "evidence_sentence": "Close is a strong fit for small sales teams.",
            },
        )
        mock_llm.assert_called_once()
        self.assertEqual(result["mention_classification"], "hit")
        self.assertTrue(result["is_hit"])
        self.assertEqual(result["evidence_sentence"], "Close is a strong fit for small sales teams.")

    def test_llm_says_yes_but_evidence_not_in_real_text_is_not_a_hit(self):
        answer_text = "Close is a strong fit for small sales teams. It combines calling, texting, and email."
        result, _mock_llm = self._run_with_answer(
            answer_text,
            [],
            disambiguation_result={
                "refers_to_company": True,
                "evidence_sentence": "This sentence was never actually in the real answer.",
            },
        )
        # A fabricated quote is never trusted, even when the model says "yes".
        self.assertEqual(result["mention_classification"], "no_mention")
        self.assertFalse(result["is_hit"])
        self.assertIsNone(result["evidence_sentence"])

    def test_citation_domain_match_skips_the_llm_entirely(self):
        result, mock_llm = self._run_with_answer(
            "Some generic answer that never names any vendor by name.",
            ["https://close.com/product"],
            disambiguation_result={"refers_to_company": False, "evidence_sentence": ""},
        )
        mock_llm.assert_not_called()
        self.assertEqual(result["mention_rule"], "citation_domain_match")
        self.assertEqual(result["mention_classification"], "hit")
        self.assertTrue(result["is_hit"])

    def test_no_candidate_sentences_skips_the_llm_entirely(self):
        result, mock_llm = self._run_with_answer(
            "Some generic answer that never uses the word at all.",
            [],
            disambiguation_result={"refers_to_company": False, "evidence_sentence": ""},
        )
        mock_llm.assert_not_called()
        self.assertEqual(result["mention_classification"], "no_mention")
        self.assertFalse(result["is_hit"])

    def test_llm_call_failure_is_marked_uncertain(self):
        result, _mock_llm = self._run_with_answer(
            "Close is a strong fit for this team.",
            [],
            disambiguation_error=requests.ConnectionError("boom"),
        )
        self.assertEqual(result["mention_classification"], "uncertain")
        self.assertIsNone(result["is_hit"])


class _ExecuteSingleRunStub:
    """
    Thread-safe stand-in for citation_engine._execute_single_run, returning
    a fixed sequence of mention_classification values per query, so the
    higher-level mention_rate/denominator logic in check_citation_visibility
    can be tested without re-deriving classifications from scratch.
    """

    def __init__(self, classifications_by_query: dict[str, list[str]]):
        self._classifications_by_query = classifications_by_query
        self._lock = threading.Lock()
        self._next_index = {query: 0 for query in classifications_by_query}

    def __call__(self, query, domain, company_name, category, name_is_common_word) -> dict:
        with self._lock:
            index = self._next_index[query]
            self._next_index[query] += 1

        classification = self._classifications_by_query[query][index]
        is_hit = None if classification == "uncertain" else classification == "hit"
        return {
            "status": "ok",
            "answer_text": f"answer for run {index} of {query}",
            "all_citations": [],
            "matched_citations": [],
            "mention_rule": "llm_disambiguation",
            "mention_classification": classification,
            "candidate_sentences": [],
            "llm_judgment": None,
            "evidence_sentence": None,
            "is_hit": is_hit,
        }


class UncertainDenominatorTests(unittest.TestCase):
    """check_citation_visibility excludes 'uncertain' runs from both the hit count and the mention_rate denominator."""

    def test_uncertain_runs_excluded_from_denominator_and_hit_count(self):
        fake_settings = Settings(perplexity_api_key="test-key", deepseek_api_key=None)
        classifications = {
            # 1 hit, 1 uncertain, 1 no_mention -> countable runs = 2, hits = 1 -> mention_rate 0.5.
            QUERIES[0]: ["hit", "uncertain", "no_mention"],
            QUERIES[1]: ["hit", "hit", "hit"],
            QUERIES[2]: ["hit", "hit", "hit"],
            QUERIES[3]: ["hit", "hit", "hit"],
            QUERIES[4]: ["hit", "hit", "hit"],
        }
        stub = _ExecuteSingleRunStub(classifications)

        with patch.object(citation_engine, "settings", fake_settings), \
             patch.object(citation_engine, "get_cached_queries", return_value=(CATEGORY, QUERIES)), \
             patch.object(citation_engine, "_execute_single_run", side_effect=stub), \
             patch.object(citation_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
            result = citation_engine.check_citation_visibility(COMPANY_NAME, DOMAIN)

        entry = next(e for e in result.details["per_query_results"] if e["query"] == QUERIES[0])
        self.assertEqual(entry["successful_runs"], 3)
        self.assertEqual(entry["uncertain_runs"], 1)
        self.assertAlmostEqual(entry["mention_rate"], 0.5)
        self.assertEqual(result.details["total_uncertain_runs"], 1)


class QueryGenerationTests(unittest.TestCase):
    """The 2-step category -> queries generation, and that homepage text never reaches the queries call."""

    def test_queries_call_never_receives_homepage_text(self):
        homepage_text = "UNIQUE_HOMEPAGE_MARKER_TEXT_12345 describing the real product in detail."
        category = "browser side security and privacy compliance monitoring"
        received_args = []

        def fake_call_for_queries(received_category):
            received_args.append(received_category)
            return list(QUERIES)

        with patch.object(citation_engine, "_call_deepseek_for_category", return_value=category), \
             patch.object(citation_engine, "_call_deepseek_for_queries", side_effect=fake_call_for_queries):
            result_category, result_queries, _log = citation_engine._generate_category_and_brand_free_queries(
                COMPANY_NAME, DOMAIN, homepage_text
            )

        self.assertEqual(result_category, category)
        self.assertEqual(result_queries, QUERIES)
        self.assertEqual(len(received_args), 1)
        # The queries call received ONLY the category string -- nothing
        # from the real homepage text leaked into it.
        self.assertEqual(received_args[0], category)
        self.assertNotIn("UNIQUE_HOMEPAGE_MARKER_TEXT_12345", received_args[0])

    def test_contaminated_batch_is_retried_without_recalling_category(self):
        homepage_text = "Some real homepage text."
        category = "browser side security and privacy compliance monitoring"
        call_count = {"n": 0}

        def fake_call_for_queries(received_category):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First attempt is contaminated with the brand name.
                return [f"{COMPANY_NAME} is great", "q2", "q3", "q4", "q5"]
            return list(QUERIES)

        with patch.object(citation_engine, "_call_deepseek_for_category", return_value=category) as mock_category, \
             patch.object(citation_engine, "_call_deepseek_for_queries", side_effect=fake_call_for_queries):
            _result_category, result_queries, _log = citation_engine._generate_category_and_brand_free_queries(
                COMPANY_NAME, DOMAIN, homepage_text
            )

        mock_category.assert_called_once()  # category classification happens exactly once, never retried
        self.assertEqual(call_count["n"], 2)  # queries regenerated once after contamination
        self.assertEqual(result_queries, QUERIES)


if __name__ == "__main__":
    unittest.main()
