"""
Tests for the shared, committed state (question sets, company
categories, judgment cache) that makes a diagnosis reproducible on any
machine: frozen per-category question sets, their automatic quality
check, and that deleting every local cache changes nothing as long as
state/ is present.

Every DeepSeek, Perplexity, and page fetch call is mocked; the state
directory and the local data directory are redirected to temp dirs.
"""

import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_THIS_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _THIS_DIR.parent  # backend/diagnose_service
_BACKEND_DIR = _SERVICE_DIR.parent  # backend/

sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))

import engines._question_sets as question_sets  # noqa: E402
import engines._state_store as state_store  # noqa: E402
import engines.citation_engine as citation_engine  # noqa: E402
import engines.evidence_engine as evidence_engine  # noqa: E402
import engines.fact_engine as fact_engine  # noqa: E402
import engines.recency_engine as recency_engine  # noqa: E402
import engines.relevance_engine as relevance_engine  # noqa: E402
import engines.technical_engine as technical_engine  # noqa: E402
import provenance  # noqa: E402
import result_cache  # noqa: E402
from shared.config import Settings  # noqa: E402

_FAKE_SETTINGS = Settings(perplexity_api_key="test-key", deepseek_api_key="test-key")
_FAKE_PLAIN_FINDING = ({"what_we_found": "stub", "what_it_costs_you": "", "fix_it_today": ""}, [])

QUESTIONS = [
    "How can I see which scripts run on my checkout page?",
    "What tools detect card skimming code on a website?",
    "How do I keep third party tags compliant with privacy laws?",
    "How can a small team audit the JavaScript on its site?",
    "What should I check before adding a new tracking script?",
]
CATEGORY = {"name": "Client side web security", "description": "Tools that monitor scripts running in visitors' browsers."}
ALL_PASS = [{"passes": True, "reason": "A real buyer would ask this."}] * 5


class _StateDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp_dir.name) / "state"
        self.data_dir = Path(self._tmp_dir.name) / "data"
        self._stack = ExitStack()
        self._stack.enter_context(patch.object(state_store, "STATE_DIR", self.state_dir))
        self._stack.enter_context(patch.object(result_cache, "_DATA_DIR", self.data_dir))
        self._stack.enter_context(patch.object(result_cache, "_CACHE_FILE", self.data_dir / "result_cache.json"))

    def tearDown(self):
        self._stack.close()
        self._tmp_dir.cleanup()


def _citation_patches(stack: ExitStack, *, queries_side_effect=None, match=None, review=ALL_PASS, homepage="Homepage text"):
    """Mock every external call citation_engine makes; returns the mocks that matter."""
    stack.enter_context(patch.object(citation_engine, "settings", _FAKE_SETTINGS))
    stack.enter_context(patch.object(citation_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING))
    stack.enter_context(
        patch.object(
            citation_engine,
            "_call_perplexity",
            return_value={"choices": [{"message": {"content": "Some other vendor."}}], "citations": []},
        )
    )
    mocks = SimpleNamespace(
        fetch=stack.enter_context(patch.object(citation_engine, "fetch_page_text", return_value=homepage)),
        category=stack.enter_context(patch.object(citation_engine, "_call_deepseek_for_category", return_value=dict(CATEGORY))),
        match=stack.enter_context(
            patch.object(
                citation_engine,
                "_call_deepseek_for_category_match",
                return_value=match or {"matched_category_id": None, "reason": "Different product."},
            )
        ),
        queries=stack.enter_context(
            patch.object(
                citation_engine,
                "_call_deepseek_for_queries",
                side_effect=queries_side_effect or (lambda description: list(QUESTIONS)),
            )
        ),
        review=stack.enter_context(
            patch.object(
                citation_engine,
                "_call_deepseek_for_question_review",
                side_effect=review if callable(review) else (lambda description, questions: list(review)),
            )
        ),
    )
    return mocks


class QuestionSetPerCategoryTests(_StateDirTestCase):
    def test_second_diagnosis_of_same_company_never_calls_llm_for_questions(self):
        with ExitStack() as stack:
            first = _citation_patches(stack)
            result1 = citation_engine.check_citation_visibility("Feroot Security", "feroot.com")
        self.assertEqual(first.queries.call_count, 1)

        with ExitStack() as stack:
            second = _citation_patches(stack)
            result2 = citation_engine.check_citation_visibility("Feroot Security", "https://www.feroot.com/")

        second.fetch.assert_not_called()
        second.category.assert_not_called()
        second.match.assert_not_called()
        second.queries.assert_not_called()
        second.review.assert_not_called()
        self.assertEqual(result2.details["queries_total"], 5)
        self.assertEqual(result1.details["question_set"]["category_id"], result2.details["question_set"]["category_id"])
        self.assertEqual(result2.details["question_set"]["source"], "existing_company_assignment")
        self.assertEqual([e["query"] for e in result2.details["per_query_results"]], QUESTIONS)

    def test_second_company_in_same_category_reuses_the_question_set(self):
        with ExitStack() as stack:
            _citation_patches(stack)
            result1 = citation_engine.check_citation_visibility("Feroot Security", "feroot.com")
        category_id = result1.details["question_set"]["category_id"]

        with ExitStack() as stack:
            second = _citation_patches(
                stack, match={"matched_category_id": category_id, "reason": "Both monitor browser scripts."}
            )
            result2 = citation_engine.check_citation_visibility("OtherCo", "otherco.com")

        second.match.assert_called_once()
        second.queries.assert_not_called()
        second.review.assert_not_called()
        self.assertEqual(result2.details["question_set"]["category_id"], category_id)
        self.assertEqual(result2.details["question_set"]["source"], "matched_existing_category")
        self.assertEqual([e["query"] for e in result2.details["per_query_results"]], QUESTIONS)

        assignment = question_sets.get_company_assignment("otherco.com")
        self.assertEqual(assignment["category_id"], category_id)
        self.assertEqual(assignment["match_decision"], {"matched_existing": True, "reason": "Both monitor browser scripts."})
        self.assertEqual(len(question_sets.list_categories()), 1)

    def test_new_category_record_has_every_required_field(self):
        with ExitStack() as stack:
            _citation_patches(stack)
            citation_engine.check_citation_visibility("Feroot Security", "feroot.com")

        [category] = question_sets.list_categories()
        record = question_sets.get_category(category["category_id"])
        self.assertEqual(record["category_name"], CATEGORY["name"])
        self.assertEqual(record["category_description"], CATEGORY["description"])
        self.assertEqual(record["current_version"], 1)
        version = record["versions"][0]
        self.assertEqual(version["version"], 1)
        self.assertEqual(version["questions"], QUESTIONS)
        self.assertEqual(version["status"], "frozen")
        self.assertRegex(version["generated_on"], r"^\d{4}-\d{2}-\d{2}$")
        reviewed = version["auto_check"]["attempts"][0]["questions"]
        self.assertTrue(all(q["llm_passes"] and q["llm_reason"] for q in reviewed))

    def test_question_set_never_expires(self):
        with ExitStack() as stack:
            _citation_patches(stack)
            citation_engine.check_citation_visibility("Feroot Security", "feroot.com")

        # Pretend the set was generated years ago: nothing may regenerate it.
        def _age(data):
            for record in data.values():
                for version in record["versions"]:
                    version["generated_on"] = "2001-01-01"

        state_store.update_state(self.state_dir / "question_sets.json", _age)

        with ExitStack() as stack:
            later = _citation_patches(stack)
            citation_engine.check_citation_visibility("Feroot Security", "feroot.com")
        later.queries.assert_not_called()

    def test_explicit_new_version_keeps_old_version(self):
        with ExitStack() as stack:
            _citation_patches(stack)
            citation_engine.check_citation_visibility("Feroot Security", "feroot.com")
        [category] = question_sets.list_categories()
        category_id = category["category_id"]

        new_record = {"questions": [q + " Now?" for q in QUESTIONS], "status": "frozen", "generated_on": "2026-10-12"}
        self.assertEqual(question_sets.add_question_set_version(category_id, new_record), 2)

        record = question_sets.get_category(category_id)
        self.assertEqual([v["version"] for v in record["versions"]], [1, 2])
        self.assertEqual(record["versions"][0]["questions"], QUESTIONS)
        self.assertEqual(question_sets.get_current_question_set(category_id)["version"], 2)


class QuestionSetAutoCheckTests(_StateDirTestCase):
    def test_failed_auto_check_is_retried_then_frozen(self):
        reviews = iter(
            [
                [{"passes": False, "reason": "Sounds like a vendor wrote it."}] + ALL_PASS[1:],
                list(ALL_PASS),
            ]
        )
        with ExitStack() as stack:
            mocks = _citation_patches(stack, review=lambda description, questions: next(reviews))
            result = citation_engine.check_citation_visibility("Feroot Security", "feroot.com")

        self.assertEqual(mocks.queries.call_count, 2)
        self.assertEqual(result.details["question_set"]["status"], "frozen")
        self.assertNotIn("note", result.details["question_set"])

    def test_three_failed_attempts_use_best_batch_marked_needs_attention(self):
        batches = iter([[f"Batch {n} question {i}?" for i in range(1, 6)] for n in (1, 2, 3)])
        pass_counts = iter([2, 4, 3])

        def review(description, questions):
            passing = next(pass_counts)
            return [
                {"passes": i < passing, "reason": "ok" if i < passing else "Not a real buyer question."}
                for i in range(5)
            ]

        with ExitStack() as stack:
            mocks = _citation_patches(stack, queries_side_effect=lambda d: next(batches), review=review)
            result = citation_engine.check_citation_visibility("Feroot Security", "feroot.com")

        self.assertEqual(mocks.queries.call_count, citation_engine.MAX_GENERATION_ATTEMPTS)
        self.assertNotEqual(result.score, -1)  # the diagnosis still runs
        info = result.details["question_set"]
        self.assertEqual(info["status"], "needs_attention")
        self.assertIn("Question set quality pending review", info["note"])
        self.assertTrue(all(e["query"].startswith("Batch 2") for e in result.details["per_query_results"]))

        stored = question_sets.get_current_question_set(info["category_id"])
        self.assertEqual(stored["status"], "needs_attention")
        self.assertEqual(stored["auto_check"]["chosen_attempt"], 2)
        self.assertEqual(len(stored["auto_check"]["attempts"]), 3)

    def test_brand_or_wrong_count_batches_are_never_stored(self):
        batches = iter(
            [
                ["Is Feroot Security good?"] + QUESTIONS[1:],
                QUESTIONS[:4],
                ["Should I buy from feroot.com?"] + QUESTIONS[1:],
            ]
        )
        with ExitStack() as stack:
            _citation_patches(stack, queries_side_effect=lambda d: next(batches))
            result = citation_engine.check_citation_visibility("Feroot Security", "feroot.com")

        self.assertEqual(result.score, -1)
        self.assertEqual(question_sets.list_categories(), [])
        self.assertIsNone(question_sets.get_company_assignment("feroot.com"))


# ---------------------------------------------------------------------------
# Deleting every local cache must not change the five reason scores.
# ---------------------------------------------------------------------------

DOMAIN = "feroot.com"
HOMEPAGE_HTML = (
    "<html><head><title>Acme</title></head><body><h1>Acme</h1><h2>A</h2><h2>B</h2>"
    "<p>We cut checkout fraud by 42% for 1,200 customers. SOC 2 certified.</p>"
    '<a href="/pricing">Pricing</a></body></html>'
)
PAGE_TEXT = "We cut checkout fraud by 42% for 1,200 customers. SOC 2 certified."


def _fake_response():
    return SimpleNamespace(
        url=f"https://{DOMAIN}/",
        text=HOMEPAGE_HTML,
        headers={"Last-Modified": "Mon, 05 Oct 2026 00:00:00 GMT"},
    )


class _Judges:
    """Stand-ins for every LLM judgment call behind the five reason dimensions; counts calls."""

    def __init__(self, seed: int):
        self.seed = seed
        self.calls = 0
        self._lock = threading.Lock()

    def _count(self):
        with self._lock:
            self.calls += 1

    def evidence(self, page_text):
        self._count()
        return [{"category": "number_or_statistic", "quote": "42%"}] * (1 + self.seed % 2)

    def relevance(self, question, pages_text):
        self._count()
        return {"classification": ["answered", "partial", "missing"][self.seed % 3], "evidence_sentence": "", "source_url": ""}

    def perplexity_fact(self, query):
        self._count()
        return {
            "choices": [{"message": {"content": f"G2 says pricing starts at ${self.seed}."}}],
            "citations": ["https://www.g2.com/products/acme"],
        }

    def fact_comparison(self, company_name, official_text, third_party_text):
        self._count()
        conflicts = [{"field": "pricing", "official_says": "a", "third_party_says": "b"}] * (self.seed % 3)
        return {"compared_fields": ["pricing"], "conflicts": conflicts}


def _run_five_reasons(judges: _Judges) -> dict:
    """Run citation (for its questions) plus the five reason engines with every outside call mocked."""
    with ExitStack() as stack:
        _citation_patches(stack, homepage=PAGE_TEXT)
        for engine in (relevance_engine, fact_engine, recency_engine, technical_engine):
            stack.enter_context(patch.object(engine, "settings", _FAKE_SETTINGS))
            stack.enter_context(patch.object(engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING))
        # evidence_engine is rule-based: only its page fetch is mocked.
        stack.enter_context(
            patch.object(evidence_engine, "_fetch_html", side_effect=lambda url: (f"https://{DOMAIN}/", HOMEPAGE_HTML))
        )
        stack.enter_context(patch.object(relevance_engine, "_fetch_homepage_response", return_value=_fake_response()))
        stack.enter_context(patch.object(relevance_engine, "fetch_page_text", return_value=PAGE_TEXT))
        stack.enter_context(
            patch.object(relevance_engine, "_call_deepseek_for_relevance_judgment", side_effect=judges.relevance)
        )
        stack.enter_context(patch.object(fact_engine, "fetch_page_text", return_value=PAGE_TEXT))
        stack.enter_context(patch.object(fact_engine, "_call_perplexity", side_effect=judges.perplexity_fact))
        stack.enter_context(
            patch.object(fact_engine, "_call_deepseek_for_fact_comparison", side_effect=judges.fact_comparison)
        )
        stack.enter_context(patch.object(recency_engine, "_fetch_homepage", return_value=_fake_response()))
        stack.enter_context(patch.object(technical_engine, "fetch_page_html", return_value=HOMEPAGE_HTML))
        stack.enter_context(
            patch.object(
                technical_engine,
                "_check_crawler_access",
                return_value={"points": 30, "allowed_crawlers": ["GPTBot"], "blocked_crawlers": []},
            )
        )

        citation = citation_engine.check_citation_visibility("Acme", DOMAIN)
        results = [
            relevance_engine.check_topic_relevance(DOMAIN, citation),
            evidence_engine.check_evidence_density(DOMAIN),
            recency_engine.check_recency(DOMAIN),
            fact_engine.check_fact_consistency("Acme", DOMAIN),
            technical_engine.check_technical_readability(DOMAIN),
        ]
    dimensions = [citation.model_dump()] + [r.model_dump() for r in results]
    result_cache.save_result(DOMAIN, {"company_name": "Acme", "domain": DOMAIN, "dimensions": dimensions})
    return {"scores": {r.dimension: r.score for r in results}, "provenance": provenance.build_provenance(dimensions)}


class DeletingLocalCachesTests(_StateDirTestCase):
    def test_five_reason_scores_unchanged_and_no_llm_judgment_after_deleting_local_caches(self):
        first_judges = _Judges(seed=1)
        first = _run_five_reasons(first_judges)
        self.assertGreater(first_judges.calls, 0)
        self.assertTrue(all(score != -1 for score in first["scores"].values()), first["scores"])
        self.assertTrue((self.data_dir / "result_cache.json").exists())

        # "Another machine": every local cache is gone, only state/ remains.
        shutil.rmtree(self.data_dir)

        # Different seed: any judgment that is NOT served from state/ would change a score.
        second_judges = _Judges(seed=2)
        second = _run_five_reasons(second_judges)

        self.assertEqual(second["scores"], first["scores"])
        self.assertEqual(second_judges.calls, 0)
        self.assertEqual(second["provenance"]["method_versions"], first["provenance"]["method_versions"])
        self.assertEqual(second["provenance"]["question_set"], first["provenance"]["question_set"])
        # citation_visibility only reads the homepage the first time a company is classified.
        first_pages = {k: v for k, v in first["provenance"]["pages"].items() if k != "citation_visibility"}
        self.assertEqual(second["provenance"]["pages"], first_pages)

    def test_provenance_records_versions_question_set_pages_and_date(self):
        result = _run_five_reasons(_Judges(seed=0))
        prov = result["provenance"]
        self.assertEqual(set(prov["method_versions"]), {
            "citation_visibility", "topic_relevance", "evidence_density",
            "content_recency", "fact_consistency", "technical_readability", "source_layers",
        })
        self.assertEqual(prov["question_set"]["version"], 1)
        self.assertRegex(prov["run_date"], r"^\d{4}-\d{2}-\d{2}$")
        for dimension in ("topic_relevance", "evidence_density", "content_recency", "fact_consistency", "technical_readability"):
            self.assertTrue(prov["pages"][dimension], dimension)
            for page in prov["pages"][dimension]:
                self.assertRegex(page["text_sha256"], r"^[0-9a-f]{64}$")


class StateStoreTests(_StateDirTestCase):
    def test_concurrent_updates_are_not_lost(self):
        path = self.state_dir / "counter.json"

        def bump():
            for _ in range(20):
                state_store.update_state(path, lambda data: data.__setitem__("n", data.get("n", 0) + 1))

        threads = [threading.Thread(target=bump) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(state_store.read_state(path), {"n": 80})


if __name__ == "__main__":
    unittest.main()
