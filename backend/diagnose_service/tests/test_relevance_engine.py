"""
Tests for relevance_engine.check_topic_relevance.

Every DeepSeek call and network request is mocked: no API key is
required, and none of this ever touches the network, so it runs the
same locally and in CI.
"""

import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

_THIS_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _THIS_DIR.parent  # backend/diagnose_service
_BACKEND_DIR = _SERVICE_DIR.parent  # backend/

sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))

import engines.relevance_engine as relevance_engine  # noqa: E402
from models import DimensionScore  # noqa: E402
from shared.config import Settings  # noqa: E402

DOMAIN = "example.com"
HOMEPAGE_URL = f"https://{DOMAIN}/"

QUESTIONS = [
    "How do I automate repetitive invoice approvals?",
    "What integrations does this support for accounting software?",
    "How much does a mid-size team typically pay per month?",
    "Can I try this before committing to a paid plan?",
    "How is customer data kept secure?",
]

HOMEPAGE_TEXT = (
    "ExampleCo helps finance teams automate repetitive invoice approvals end to end, cutting manual "
    "review time significantly. It connects directly to common accounting software so approvals sync "
    "automatically without any manual data entry. Plans start affordably for small teams and scale up "
    "as a company grows, with a real free trial available before anyone has to commit to a paid plan."
)

_REAL_EVIDENCE_SENTENCE = (
    "ExampleCo helps finance teams automate repetitive invoice approvals end to end, cutting manual "
    "review time significantly."
)

_HOMEPAGE_HTML_FOR_LINKS = """
<html><body>
<nav><a href="/">Home</a><a href="/pricing">Pricing</a><a href="/features">Features</a><a href="/about">About</a></nav>
</body></html>
"""

_FAKE_PLAIN_FINDING = ({"what_we_found": "stub", "what_it_costs_you": "", "fix_it_today": ""}, [])
_FAKE_SETTINGS = Settings(perplexity_api_key=None, deepseek_api_key="test-key")


def _citation_result_with_questions(questions: list[str], citation_score: int = 40) -> DimensionScore:
    per_query_results = [{"query": q} for q in questions]
    return DimensionScore(
        dimension="citation_visibility",
        score=citation_score,
        details={"per_query_results": per_query_results},
    )


def _fake_homepage_response() -> MagicMock:
    response = MagicMock()
    response.url = HOMEPAGE_URL
    response.text = _HOMEPAGE_HTML_FOR_LINKS
    response.raise_for_status = MagicMock()
    return response


def _always(classification: str, evidence: str = "", source_url: str = ""):
    """A DeepSeek side_effect that returns the same judgment every call."""
    def _call(question, pages_text):
        return {"classification": classification, "evidence_sentence": evidence, "source_url": source_url}
    return _call


def _run_with(citation_result, deepseek_side_effect, page_texts=None, homepage_fetch_error=None):
    page_texts = page_texts or {}

    def fake_fetch_page_text(url, method="main"):
        return page_texts.get(url, "")

    with ExitStack() as stack:
        stack.enter_context(patch.object(relevance_engine, "settings", _FAKE_SETTINGS))
        stack.enter_context(patch.object(relevance_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING))
        stack.enter_context(patch.object(relevance_engine, "extract_text_from_html", return_value=HOMEPAGE_TEXT))
        stack.enter_context(patch.object(relevance_engine, "fetch_page_text", side_effect=fake_fetch_page_text))
        stack.enter_context(
            patch.object(relevance_engine, "_call_deepseek_for_relevance_judgment", side_effect=deepseek_side_effect)
        )
        if homepage_fetch_error is not None:
            stack.enter_context(patch.object(relevance_engine, "_fetch_homepage_response", side_effect=homepage_fetch_error))
        else:
            stack.enter_context(
                patch.object(relevance_engine, "_fetch_homepage_response", return_value=_fake_homepage_response())
            )
        return relevance_engine.check_topic_relevance(DOMAIN, citation_result)


class RelevanceEngineTests(unittest.TestCase):
    def test_all_questions_answered(self):
        citation_result = _citation_result_with_questions(QUESTIONS)
        deepseek = _always("answered", _REAL_EVIDENCE_SENTENCE, HOMEPAGE_URL)

        result = _run_with(citation_result, deepseek)

        self.assertEqual(result.score, 100)
        self.assertEqual(len(result.details["per_question_results"]), 5)
        for entry in result.details["per_question_results"]:
            self.assertEqual(entry["classification"], "answered")
            self.assertEqual(entry["evidence_sentence"], _REAL_EVIDENCE_SENTENCE)
        self.assertEqual(result.details["unanswered_questions"], [])

    def test_all_questions_missing(self):
        citation_result = _citation_result_with_questions(QUESTIONS)
        deepseek = _always("missing")

        result = _run_with(citation_result, deepseek)

        self.assertEqual(result.score, 0)
        self.assertEqual(len(result.details["unanswered_questions"]), 5)

    def test_three_runs_disagree_counts_as_partial(self):
        citation_result = _citation_result_with_questions(QUESTIONS)
        call_count = {"n": 0}

        def deepseek(question, pages_text):
            if question != QUESTIONS[0]:
                return {"classification": "answered", "evidence_sentence": _REAL_EVIDENCE_SENTENCE, "source_url": HOMEPAGE_URL}
            call_count["n"] += 1
            # 3 runs on the same question, each landing on a different classification.
            sequence = [
                {"classification": "answered", "evidence_sentence": _REAL_EVIDENCE_SENTENCE, "source_url": HOMEPAGE_URL},
                {"classification": "partial", "evidence_sentence": _REAL_EVIDENCE_SENTENCE, "source_url": HOMEPAGE_URL},
                {"classification": "missing", "evidence_sentence": "", "source_url": ""},
            ]
            return sequence[call_count["n"] - 1]

        result = _run_with(citation_result, deepseek)

        first_question_result = result.details["per_question_results"][0]
        self.assertEqual(first_question_result["classification"], "partial")
        self.assertEqual(call_count["n"], 3)

    def test_fabricated_evidence_downgrades_to_missing(self):
        citation_result = _citation_result_with_questions(QUESTIONS)
        deepseek = _always(
            "answered",
            "This sentence absolutely does not appear anywhere on the real page text.",
            HOMEPAGE_URL,
        )

        result = _run_with(citation_result, deepseek)

        for entry in result.details["per_question_results"]:
            self.assertEqual(entry["classification"], "missing")
            self.assertIsNone(entry["evidence_sentence"])
            for run in entry["runs"]:
                self.assertFalse(run["evidence_verified"])

    def test_homepage_fetch_failure_returns_insufficient_data(self):
        citation_result = _citation_result_with_questions(QUESTIONS)

        result = _run_with(
            citation_result,
            _always("answered"),
            homepage_fetch_error=requests.ConnectionError("boom"),
        )

        self.assertEqual(result.score, -1)
        self.assertEqual(result.details["status"], "insufficient_data")

    def test_no_questions_from_citation_engine_returns_insufficient_data(self):
        citation_result = DimensionScore(
            dimension="citation_visibility",
            score=-1,
            details={"reason": "PERPLEXITY_API_KEY is not configured; no query was performed."},
        )

        with patch.object(relevance_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
            result = relevance_engine.check_topic_relevance(DOMAIN, citation_result)

        self.assertEqual(result.score, -1)
        self.assertEqual(result.details["status"], "insufficient_data")

    def test_works_even_when_citation_visibility_itself_is_insufficient_data(self):
        # citation_visibility's own score is -1 (e.g. every Perplexity call
        # failed), but question generation itself succeeded, so
        # per_query_results is still there and this engine should still run.
        citation_result = _citation_result_with_questions(QUESTIONS, citation_score=-1)

        result = _run_with(citation_result, _always("answered", _REAL_EVIDENCE_SENTENCE, HOMEPAGE_URL))

        self.assertNotEqual(result.score, -1)
        self.assertEqual(result.score, 100)

    def test_deterministic_output_for_same_mock_input(self):
        citation_result = _citation_result_with_questions(QUESTIONS)
        deepseek = _always("answered", _REAL_EVIDENCE_SENTENCE, HOMEPAGE_URL)

        first = _run_with(citation_result, deepseek)
        second = _run_with(citation_result, deepseek)

        self.assertEqual(first.score, second.score)
        self.assertEqual(first.details["per_question_results"], second.details["per_question_results"])


class UrlNormalizationTests(unittest.TestCase):
    def test_trailing_slash_www_and_fragment_all_normalize_the_same(self):
        normalized = {
            relevance_engine._normalize_url("example.com/"),
            relevance_engine._normalize_url("https://www.example.com"),
            relevance_engine._normalize_url("example.com/#top"),
        }

        self.assertEqual(normalized, {"https://example.com"})

    def test_different_paths_stay_distinct(self):
        self.assertNotEqual(
            relevance_engine._normalize_url("https://www.example.com/pricing"),
            relevance_engine._normalize_url("https://www.example.com/features"),
        )


class SelectAdditionalPagesDedupTests(unittest.TestCase):
    def test_trailing_slash_variants_of_same_page_are_not_double_counted(self):
        from bs4 import BeautifulSoup

        html = """
        <html><body>
        <a href="https://example.com/pricing">Pricing</a>
        <a href="https://example.com/pricing/">Pricing again</a>
        <a href="https://example.com/features">Features</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")

        pages = relevance_engine._select_additional_pages(soup, "https://example.com/")

        # The two pricing links normalize to the same page, so only one
        # of them is kept, and the features link fills the slot that
        # would otherwise have been wasted on the duplicate.
        self.assertEqual(len(pages), 2)
        normalized_pages = {relevance_engine._normalize_url(p) for p in pages}
        self.assertEqual(normalized_pages, {"https://example.com/pricing", "https://example.com/features"})

    def test_link_back_to_homepage_in_any_form_is_excluded(self):
        from bs4 import BeautifulSoup

        html = """
        <html><body>
        <a href="https://www.example.com">Home</a>
        <a href="https://example.com/">Home again</a>
        <a href="https://example.com/pricing">Pricing</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")

        pages = relevance_engine._select_additional_pages(soup, "https://example.com/")

        self.assertEqual(pages, ["https://example.com/pricing"])


if __name__ == "__main__":
    unittest.main()
