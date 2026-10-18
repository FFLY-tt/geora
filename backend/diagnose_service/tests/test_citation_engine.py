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
sys.path.insert(0, str(_THIS_DIR))

import engines.citation_engine as citation_engine  # noqa: E402
from shared.config import Settings  # noqa: E402
from _question_fixtures import QUESTIONS_15, distinct_questions  # noqa: E402

COMPANY_NAME = "ExampleCo"
DOMAIN = "exampleco.com"
CATEGORY = "browser side security and privacy compliance monitoring"
QUERIES = list(QUESTIONS_15)

_FAKE_QUESTION_SET = (
    {
        "category_id": "browser-script-security",
        "category_name": "Browser script security",
        "category_description": CATEGORY,
        "version": 1,
        "status": "frozen",
        "questions": QUERIES,
        "source": "existing_company_assignment",
    },
    {},
)

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
         patch.object(citation_engine, "_resolve_question_set", return_value=_FAKE_QUESTION_SET), \
         patch.object(citation_engine, "_call_perplexity", side_effect=stub), \
         patch.object(citation_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
        result = citation_engine.check_citation_visibility(COMPANY_NAME, DOMAIN)

    return result, stub


def _patterns(hits: int, misses: int = 0, fails: int = 0) -> dict[str, list[str]]:
    """One outcome per question (each question is asked exactly once), in question order."""
    outcomes = ["hit"] * hits + ["miss"] * misses + ["fail"] * fails
    assert len(outcomes) == len(QUERIES)
    return {query: [outcome] for query, outcome in zip(QUERIES, outcomes)}


class CitationEngineSingleRunTests(unittest.TestCase):
    def test_score_from_mixed_hit_patterns(self):
        # 6 of 15 answers mention the company -> mention rate 0.4 -> score 40.
        result, stub = _run_with_patterns(_patterns(hits=6, misses=9))

        self.assertEqual(result.score, 40)
        self.assertEqual(stub.call_count, 15)
        self.assertEqual(result.details["queries_valid"], 15)

    def test_failed_question_is_left_out_of_the_denominator(self):
        result, _ = _run_with_patterns(_patterns(hits=14, fails=1))

        failed = result.details["fully_failed_queries"]
        self.assertEqual(failed, [QUERIES[14]])
        self.assertEqual(result.details["queries_valid"], 14)
        self.assertEqual(result.score, 100)

    def test_all_calls_failed_returns_insufficient_data(self):
        result, stub = _run_with_patterns(_patterns(hits=0, fails=15))

        self.assertEqual(result.score, -1)
        self.assertEqual(result.details["status"], "insufficient_data")
        self.assertEqual(stub.call_count, 15)

    def test_deterministic_output_for_same_mock_input(self):
        first, _ = _run_with_patterns(_patterns(hits=5, misses=10))
        second, _ = _run_with_patterns(_patterns(hits=5, misses=10))

        self.assertEqual(first.score, second.score)
        self.assertEqual(first.details["average_mention_rate"], second.details["average_mention_rate"])
        self.assertEqual(first.details["per_query_results"], second.details["per_query_results"])

    def test_each_question_is_asked_exactly_once(self):
        asked = []
        real_stub = _PerplexityStub(_patterns(hits=7, misses=8))

        def recording(query):
            asked.append(query)
            return real_stub(query)

        fake_settings = Settings(perplexity_api_key="test-key", deepseek_api_key=None)
        with patch.object(citation_engine, "settings", fake_settings),              patch.object(citation_engine, "_resolve_question_set", return_value=_FAKE_QUESTION_SET),              patch.object(citation_engine, "_call_perplexity", side_effect=recording),              patch.object(citation_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
            citation_engine.check_citation_visibility(COMPANY_NAME, DOMAIN)

        self.assertEqual(citation_engine.RUNS_PER_QUERY, 1)
        self.assertEqual(len(asked), 15)
        self.assertEqual(sorted(asked), sorted(QUERIES))


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
    """check_citation_visibility excludes 'uncertain' answers from both the hit count and the mention_rate denominator."""

    def test_uncertain_answers_excluded_from_denominator_and_hit_count(self):
        fake_settings = Settings(perplexity_api_key="test-key", deepseek_api_key=None)
        # 3 hits, 1 uncertain, 11 no_mention -> countable answers = 14, hits = 3.
        classifications = {query: ["no_mention"] for query in QUERIES}
        for query in QUERIES[:3]:
            classifications[query] = ["hit"]
        classifications[QUERIES[3]] = ["uncertain"]
        stub = _ExecuteSingleRunStub(classifications)

        with patch.object(citation_engine, "settings", fake_settings),              patch.object(citation_engine, "_resolve_question_set", return_value=_FAKE_QUESTION_SET),              patch.object(citation_engine, "_execute_single_run", side_effect=stub),              patch.object(citation_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
            result = citation_engine.check_citation_visibility(COMPANY_NAME, DOMAIN)

        entry = next(e for e in result.details["per_query_results"] if e["query"] == QUERIES[3])
        self.assertEqual(entry["uncertain_runs"], 1)
        self.assertEqual(result.details["total_uncertain_runs"], 1)
        self.assertEqual(result.details["queries_valid"], 14)
        self.assertAlmostEqual(result.details["average_mention_rate"], 3 / 14, places=2)


def _all_pass(count: int) -> list[dict]:
    return [{"passes": True, "reason": "A buyer would ask this."}] * count


class QueryGenerationTests(unittest.TestCase):
    """generate_checked_question_set: 15 questions, brand-free retries, duplicates, extension of an older set."""

    BRANDS = [{"company_name": COMPANY_NAME, "domain": DOMAIN}]

    def test_queries_call_never_receives_homepage_text(self):
        received_args = []

        def fake_call_for_queries(received_category, count=None, existing=(), avoid_names=()):
            received_args.append((received_category, count, list(existing)))
            return list(QUERIES)

        with patch.object(citation_engine, "_call_deepseek_for_queries", side_effect=fake_call_for_queries),              patch.object(citation_engine, "_call_deepseek_for_question_review", return_value=_all_pass(15)):
            record, _attempts = citation_engine.generate_checked_question_set(CATEGORY, self.BRANDS)

        self.assertEqual(record["questions"], QUERIES)
        self.assertEqual(record["status"], "frozen")
        # The queries call received ONLY the category description and the number wanted.
        self.assertEqual(received_args, [(CATEGORY, 15, [])])

    def test_contaminated_batch_is_retried(self):
        call_count = {"n": 0}

        def fake_call_for_queries(received_category, count=None, existing=(), avoid_names=()):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return [f"{COMPANY_NAME} is great"] + distinct_questions(0, 14)
            return list(QUERIES)

        with patch.object(citation_engine, "_call_deepseek_for_queries", side_effect=fake_call_for_queries),              patch.object(citation_engine, "_call_deepseek_for_question_review", return_value=_all_pass(15)):
            record, attempts = citation_engine.generate_checked_question_set(CATEGORY, self.BRANDS)

        self.assertEqual(call_count["n"], 2)
        self.assertFalse(attempts[0]["usable"])
        self.assertEqual(record["questions"], QUERIES)

    def test_wrong_count_is_never_usable(self):
        with patch.object(citation_engine, "_call_deepseek_for_queries", return_value=QUERIES[:5]),              patch.object(citation_engine, "_call_deepseek_for_question_review") as review:
            record, attempts = citation_engine.generate_checked_question_set(CATEGORY, self.BRANDS)

        self.assertIsNone(record)
        self.assertEqual(len(attempts), citation_engine.MAX_GENERATION_ATTEMPTS)
        review.assert_not_called()

    def test_reworded_question_in_one_batch_fails_that_question(self):
        batch = list(QUERIES)
        batch[1] = "What is the best tool to monitor third party scripts on a checkout page today?"  # reword of question 0
        with patch.object(citation_engine, "_call_deepseek_for_question_review", return_value=_all_pass(15)):
            attempt = citation_engine._check_question_batch(batch, CATEGORY, self.BRANDS)

        self.assertFalse(attempt["all_passed"])
        self.assertEqual(attempt["questions"][1]["duplicate_of"], QUERIES[0])
        self.assertEqual(attempt["passed_count"], 14)

    def test_extension_keeps_old_questions_and_adds_only_the_missing_ones(self):
        kept = QUERIES[:5]
        added = QUERIES[5:]
        seen = {}

        def fake_call_for_queries(received_category, count=None, existing=(), avoid_names=()):
            seen["count"], seen["existing"] = count, list(existing)
            return list(added)

        with patch.object(citation_engine, "_call_deepseek_for_queries", side_effect=fake_call_for_queries),              patch.object(citation_engine, "_call_deepseek_for_question_review", return_value=_all_pass(10)):
            record, _ = citation_engine.generate_checked_question_set(CATEGORY, self.BRANDS, existing_questions=kept)

        self.assertEqual(seen, {"count": 10, "existing": kept})
        self.assertEqual(record["questions"], QUERIES)
        self.assertEqual(record["questions"][:5], kept)
        self.assertEqual(record["kept_questions_from_earlier_version"], 5)
        self.assertEqual(len(set(record["questions"])), 15)

    def test_extension_retry_keeps_passing_questions_and_only_replaces_failed_ones(self):
        kept = QUERIES[:5]
        first_batch = QUERIES[5:]
        calls = []

        def fake_call_for_queries(received_category, count=None, existing=(), avoid_names=()):
            calls.append((count, list(existing)))
            return list(first_batch) if len(calls) == 1 else distinct_questions(0, count)

        def fake_review(description, questions, existing=()):
            if len(calls) == 1:  # first batch: the last two are called paraphrases
                return [{"passes": i < 8, "reason": "ok" if i < 8 else "Paraphrase."} for i in range(len(questions))]
            return _all_pass(len(questions))

        with patch.object(citation_engine, "_call_deepseek_for_queries", side_effect=fake_call_for_queries),              patch.object(citation_engine, "_call_deepseek_for_question_review", side_effect=fake_review):
            record, attempts = citation_engine.generate_checked_question_set(CATEGORY, self.BRANDS, existing_questions=kept)

        self.assertEqual([count for count, _ in calls], [10, 2])
        self.assertEqual(calls[1][1], kept + first_batch[:8])  # the 8 that passed were kept
        self.assertEqual(record["questions"], kept + first_batch[:8] + distinct_questions(0, 2))
        self.assertEqual(record["status"], "frozen")
        self.assertEqual(record["kept_questions_from_earlier_version"], 5)
        self.assertEqual(len(set(record["questions"])), 15)

    def test_how_to_and_definition_questions_are_not_buying_questions(self):
        for question in (
            "How to follow up with sales leads?",
            "What is client-side security?",
            "How do I stop losing track of leads when my sales team gets busy?",
            "What is the best way to track if a prospect opened my video email?",
            "Why do deals go cold after a demo?",
            "Can I record my screen and webcam at the same time?",
            "How do I know which third party scripts are collecting visitor data on my site?",
        ):
            self.assertIsNotNone(citation_engine.buying_question_issue(question), question)

    def test_buying_questions_pass_the_rule(self):
        for question in (
            "What is the best CRM for a 10-person sales team?",
            "CRM tools for a startup with no sales process?",
            "Alternatives to Salesforce for small teams?",
            "HubSpot vs Pipedrive for a 5-person team?",
            "What software helps sales teams organize their leads?",
            "Which CRM is easiest to set up for a team with no IT support?",
            "Which sales CRM should I choose if compliance and call recording matter?",
        ):
            self.assertIsNone(citation_engine.buying_question_issue(question), question)

    def test_a_how_to_question_in_a_batch_fails_even_when_the_reviewer_passes_it(self):
        batch = list(QUERIES)
        batch[3] = "How to follow up with sales leads?"
        with patch.object(citation_engine, "_call_deepseek_for_question_review", return_value=_all_pass(15)):
            attempt = citation_engine._check_question_batch(batch, CATEGORY, self.BRANDS)

        self.assertFalse(attempt["all_passed"])
        self.assertTrue(attempt["usable"])
        self.assertIsNotNone(attempt["questions"][3]["not_a_buying_question"])
        self.assertEqual(attempt["passed_count"], 14)

    def test_a_question_with_the_company_name_is_blocked(self):
        for question in (f"Is {COMPANY_NAME} the best tool for small teams?", f"Alternatives to {COMPANY_NAME} for startups?", f"Best tool for {DOMAIN} users?"):
            batch = list(QUERIES)
            batch[0] = question
            with patch.object(citation_engine, "_call_deepseek_for_question_review", return_value=_all_pass(15)):
                attempt = citation_engine._check_question_batch(batch, CATEGORY, self.BRANDS)
            self.assertFalse(attempt["usable"], question)
            self.assertTrue(attempt["questions"][0]["blocking_issues"], question)

    def test_the_review_prompt_asks_for_buying_questions(self):
        for prompt in (citation_engine._QUESTION_REVIEW_SYSTEM_PROMPT, citation_engine._query_system_prompt(15)):
            self.assertIn("alternatives to", prompt.lower())
            self.assertIn("how-to", prompt.lower())

    def test_refresh_keeps_buying_questions_and_replaces_the_rest(self):
        current = ["How can I keep track of all my leads in one place?", QUERIES[0], "What is client-side security?"] + QUERIES[1:13]
        seen = {}

        def fake_call_for_queries(received_category, count=None, existing=(), avoid_names=()):
            seen["count"], seen["existing"] = count, list(existing)
            return QUERIES[13:15]

        def fake_review(description, questions, existing=()):
            return _all_pass(len(questions))

        with patch.object(citation_engine, "_call_deepseek_for_queries", side_effect=fake_call_for_queries),              patch.object(citation_engine, "_call_deepseek_for_question_review", side_effect=fake_review):
            record, attempts, review = citation_engine.refresh_question_set(CATEGORY, self.BRANDS, current)

        self.assertEqual([entry["keep"] for entry in review[:3]], [False, True, False])
        self.assertEqual(seen["count"], 2)
        self.assertEqual(seen["existing"], [QUERIES[0]] + QUERIES[1:13])
        self.assertEqual(record["status"], "frozen")
        self.assertEqual(record["questions"], QUERIES[:13] + QUERIES[13:15])
        self.assertEqual(record["kept_questions_from_earlier_version"], 13)
        self.assertTrue(all(citation_engine.buying_question_issue(q) is None for q in record["questions"]))

    def test_refresh_with_nothing_to_replace_asks_for_no_new_questions(self):
        with patch.object(citation_engine, "_call_deepseek_for_queries") as generate,              patch.object(citation_engine, "_call_deepseek_for_question_review", return_value=_all_pass(15)):
            record, attempts, review = citation_engine.refresh_question_set(CATEGORY, self.BRANDS, list(QUERIES))
        generate.assert_not_called()
        self.assertEqual(record["questions"], QUERIES)
        self.assertEqual(record["status"], "frozen")

    def test_extension_rejects_a_new_question_that_repeats_a_kept_one(self):
        kept = QUERIES[:5]
        batch = distinct_questions(0, 9) + ["Best tool to monitor third party scripts on my checkout page?"]
        with patch.object(citation_engine, "_call_deepseek_for_question_review", return_value=_all_pass(10)):
            attempt = citation_engine._check_question_batch(batch, CATEGORY, self.BRANDS, kept)

        self.assertFalse(attempt["all_passed"])
        self.assertEqual(attempt["questions"][9]["duplicate_of"], QUERIES[0])


if __name__ == "__main__":
    unittest.main()
