"""
Tests for the optional competitor comparison: input parsing, counting in the
answers already collected (same rule as for the user's own company), and that
a changed competitor list never causes a new Perplexity call. Everything
external is mocked; no key or network is needed.
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

_THIS_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _THIS_DIR.parent
_BACKEND_DIR = _SERVICE_DIR.parent

sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))
sys.path.insert(0, str(_THIS_DIR))

import competitors  # noqa: E402
import engines.citation_engine as citation_engine  # noqa: E402
import result_cache  # noqa: E402
import server  # noqa: E402
from models import DiagnosisRequest, DimensionScore  # noqa: E402
from shared.config import Settings  # noqa: E402
from _question_fixtures import QUESTIONS_15  # noqa: E402

COMPANY = "ExampleCo"
DOMAIN = "exampleco.com"
_FAKE_PLAIN_FINDING = ({"what_we_found": "stub", "what_it_costs_you": "", "fix_it_today": ""}, [])

# 15 canned answers: ExampleCo in 6, HubSpot in 10, Pipedrive in 3 (one only through a cited page),
# Close never as a product but "close more deals" in 2 answers.
_ANSWERS = {}
for _i, _q in enumerate(QUESTIONS_15):
    parts = []
    cites = ["https://unrelated-example.org/page"]
    if _i < 6:
        parts.append(f"{COMPANY} is a strong option for this.")
    if _i % 3 != 2 and _i < 14:  # 10 answers: 0,1,3,4,6,7,9,10,12,13
        parts.append("HubSpot also covers it well.")
    if _i in (0, 5):
        parts.append("Pipedrive is another common pick.")
    if _i == 8:
        cites.append("https://www.pipedrive.com/en/features")
    if _i in (10, 11):
        parts.append("A good tool helps your team close more deals.")
    if not parts:
        parts.append("Some other vendor handles this.")
    _ANSWERS[_q] = {"text": " ".join(parts), "citations": cites}


def _perplexity(query: str) -> dict:
    answer = _ANSWERS[query]
    return {"choices": [{"message": {"content": answer["text"]}}], "citations": answer["citations"]}


_FAKE_QUESTION_SET = (
    {
        "category_id": "crm",
        "category_name": "CRM",
        "category_description": "sales CRM software",
        "version": 2,
        "status": "frozen",
        "questions": QUESTIONS_15,
        "source": "existing_company_assignment",
    },
    {},
)


def _citation_result():
    """A real check_citation_visibility run over the canned answers, plus the call counter."""
    calls = []

    def counting(query):
        calls.append(query)
        return _perplexity(query)

    with patch.object(citation_engine, "settings", Settings(perplexity_api_key="k", deepseek_api_key=None)), \
         patch.object(citation_engine, "_resolve_question_set", return_value=_FAKE_QUESTION_SET), \
         patch.object(citation_engine, "_call_perplexity", side_effect=counting), \
         patch.object(citation_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING):
        result = citation_engine.check_citation_visibility(COMPANY, DOMAIN)
    return result, calls


def _not_referring(*_args, **_kwargs):
    return {"refers_to_company": False, "evidence_sentence": ""}


class ParseCompetitorsTests(unittest.TestCase):
    def test_empty_input_means_no_competitors(self):
        for raw in (None, "", "   ", " , , "):
            self.assertEqual(competitors.parse_competitors(raw, COMPANY, DOMAIN), [])

    def test_names_and_optional_domains(self):
        parsed = competitors.parse_competitors("HubSpot, Pipedrive (pipedrive.com),Monday.com (https://www.Monday.com/x)", COMPANY, DOMAIN)
        self.assertEqual(
            parsed,
            [
                {"name": "HubSpot", "domain": ""},
                {"name": "Pipedrive", "domain": "pipedrive.com"},
                {"name": "Monday.com", "domain": "monday.com"},
            ],
        )

    def test_repeats_and_own_company_are_dropped(self):
        parsed = competitors.parse_competitors("HubSpot, hubspot, ExampleCo, Other (exampleco.com), Rival", COMPANY, DOMAIN)
        self.assertEqual([c["name"] for c in parsed], ["HubSpot", "Rival"])

    def test_more_than_five_is_refused(self):
        with self.assertRaises(competitors.CompetitorInputError):
            competitors.parse_competitors("A, B, C, D, E, F", COMPANY, DOMAIN)
        self.assertEqual(len(competitors.parse_competitors("A, B, C, D, E", COMPANY, DOMAIN)), 5)

    def test_bad_domain_is_refused(self):
        with self.assertRaises(competitors.CompetitorInputError):
            competitors.parse_competitors("HubSpot (hubspot)", COMPANY, DOMAIN)

    def test_signature_is_stable_and_empty_for_none(self):
        a = competitors.parse_competitors("HubSpot, Pipedrive", COMPANY, DOMAIN)
        b = competitors.parse_competitors("hubspot,  Pipedrive ", COMPANY, DOMAIN)
        self.assertEqual(competitors.signature(a), competitors.signature(b))
        self.assertEqual(competitors.signature([]), "")
        self.assertEqual(competitors.signature(None), "")


class CompareCompetitorsTests(unittest.TestCase):
    def setUp(self):
        self.result, self.calls = _citation_result()
        self.details = self.result.details

    def test_no_competitors_means_no_block_and_no_matching_work(self):
        with patch.object(citation_engine, "classify_mention") as classify:
            self.assertIsNone(competitors.compare_competitors(COMPANY, DOMAIN, [], self.details))
        classify.assert_not_called()

    def test_counts_for_you_and_each_competitor_sorted_high_to_low(self):
        parsed = competitors.parse_competitors("Pipedrive (pipedrive.com), HubSpot", COMPANY, DOMAIN)
        block = competitors.compare_competitors(COMPANY, DOMAIN, parsed, self.details)

        counts = [(e["name"], e["mentions"], e["total"]) for e in block["entries"]]
        self.assertEqual(counts, [("HubSpot", 10, 15), (COMPANY, 6, 15), ("Pipedrive", 3, 15)])
        self.assertEqual(block["competitors"], parsed)
        you = next(e for e in block["entries"] if e["is_you"])
        self.assertEqual(you["mentions"], self.details["mentions"])  # same figure as the mention rate
        self.assertEqual(len(you["mentioned_in"]), 6)
        pipedrive = next(e for e in block["entries"] if e["name"] == "Pipedrive")
        self.assertIn("Pipedrive is another common pick.", [m["snippet"] for m in pipedrive["mentioned_in"]])
        self.assertTrue(all(m["question"] in QUESTIONS_15 for m in pipedrive["mentioned_in"]))

    def test_a_cited_page_counts_when_a_domain_is_given(self):
        with_domain = competitors.compare_competitors(
            COMPANY, DOMAIN, [{"name": "Pipedrive", "domain": "pipedrive.com"}], self.details
        )
        without_domain = competitors.compare_competitors(
            COMPANY, DOMAIN, [{"name": "Pipedrive", "domain": ""}], self.details
        )
        counted = lambda block: next(e for e in block["entries"] if e["name"] == "Pipedrive")["mentions"]
        self.assertEqual(counted(with_domain), 3)  # answers 0 and 5 by name, answer 8 through the cited page
        self.assertEqual(counted(without_domain), 2)

    def test_common_word_name_follows_the_same_rule_as_for_your_own_company(self):
        """"close more deals" is not the company Close: the shared judge decides, exactly as for the user's own company."""
        with patch.object(citation_engine, "_call_deepseek_for_common_word_disambiguation", side_effect=_not_referring) as judge:
            block = competitors.compare_competitors(COMPANY, DOMAIN, [{"name": "Close", "domain": ""}], self.details)
        close = next(e for e in block["entries"] if e["name"] == "Close")
        self.assertEqual((close["mentions"], close["total"]), (0, 15))
        self.assertEqual(judge.call_count, 2)  # only the two answers that contain the word at all

        # the very same function and verdict as for a company with this name
        answer = _ANSWERS[QUESTIONS_15[10]]
        with patch.object(citation_engine, "_call_deepseek_for_common_word_disambiguation", side_effect=_not_referring):
            own_rule = citation_engine.classify_mention("Close", "close.com", "sales CRM software", answer["text"], answer["citations"])
        self.assertEqual(own_rule["mention_classification"], "no_mention")
        self.assertEqual(own_rule["mention_rule"], "llm_disambiguation")

    def test_common_word_name_with_its_domain_cited_counts_without_a_judge(self):
        details = {
            "category": "sales CRM software",
            "per_query_results": [
                {"query": "q", "runs": [{"status": "ok", "answer_text": "See the vendor.", "all_citations": ["https://close.com/crm"],
                                          "mention_classification": "no_mention", "is_hit": False, "mention_rule": "whole_word"}]}
            ],
        }
        with patch.object(citation_engine, "_call_deepseek_for_common_word_disambiguation") as judge:
            block = competitors.compare_competitors(COMPANY, DOMAIN, [{"name": "Close", "domain": "close.com"}], details)
        judge.assert_not_called()
        self.assertEqual(next(e for e in block["entries"] if e["name"] == "Close")["mentions"], 1)

    def test_an_unsure_judgement_is_left_out_of_the_count(self):
        def broken(*_args, **_kwargs):
            raise requests.ConnectionError("down")

        with patch.object(citation_engine, "_call_deepseek_for_common_word_disambiguation", side_effect=broken):
            block = competitors.compare_competitors(COMPANY, DOMAIN, [{"name": "Close", "domain": ""}], self.details)
        close = next(e for e in block["entries"] if e["name"] == "Close")
        self.assertEqual((close["mentions"], close["total"]), (0, 13))

    def test_same_input_twice_gives_identical_results(self):
        parsed = competitors.parse_competitors("Pipedrive (pipedrive.com), HubSpot", COMPANY, DOMAIN)
        first = competitors.compare_competitors(COMPANY, DOMAIN, parsed, self.details)
        second = competitors.compare_competitors(COMPANY, DOMAIN, parsed, _citation_result()[0].details)
        self.assertEqual(first, second)

    def test_snippet_is_one_sentence_and_shortened(self):
        long_sentence = "ExampleCo " + "word " * 100 + "end."
        details = {
            "per_query_results": [
                {"query": "q", "runs": [{"status": "ok", "answer_text": "Intro. " + long_sentence, "all_citations": [],
                                          "mention_classification": "hit", "is_hit": True, "mention_rule": "whole_word"}]}
            ]
        }
        block = competitors.compare_competitors(COMPANY, DOMAIN, [{"name": "Rival", "domain": ""}], details)
        snippet = block["entries"][0]["mentioned_in"][0]["snippet"] if block["entries"][0]["is_you"] else ""
        self.assertTrue(snippet.startswith("ExampleCo word"))
        self.assertLessEqual(len(snippet), competitors.SNIPPET_LENGTH)
        self.assertTrue(snippet.endswith("…"))


class ServerCompetitorFlowTests(unittest.TestCase):
    """The /api/diagnose route: no competitors -> no block; a changed list recounts from the cache without a Perplexity call."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        data = Path(self._tmp.name)
        self._patches = [
            patch.object(result_cache, "_DATA_DIR", data),
            patch.object(result_cache, "_CACHE_FILE", data / "result_cache.json"),
        ]
        for p in self._patches:
            p.start()
        self.perplexity_calls = []

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def _diagnose(self, competitor_text):
        def counting(query):
            self.perplexity_calls.append(query)
            return _perplexity(query)

        def other(name):
            return SimpleNamespace(model_dump=lambda: DimensionScore(dimension=name, score=50, details={}).model_dump())

        with patch.object(citation_engine, "settings", Settings(perplexity_api_key="k", deepseek_api_key=None)), \
             patch.object(citation_engine, "_resolve_question_set", return_value=_FAKE_QUESTION_SET), \
             patch.object(citation_engine, "_call_perplexity", side_effect=counting), \
             patch.object(citation_engine, "make_plain_finding", return_value=_FAKE_PLAIN_FINDING), \
             patch.object(server, "check_citation_visibility", citation_engine.check_citation_visibility), \
             patch.object(server, "check_evidence_density", lambda domain: other("evidence_density")), \
             patch.object(server, "check_recency", lambda domain: other("content_recency")), \
             patch.object(server, "check_fact_consistency", lambda name, domain: other("fact_consistency")), \
             patch.object(server, "check_technical_readability", lambda domain: other("technical_readability")), \
             patch.object(server, "check_topic_relevance", lambda domain, citation: other("topic_relevance")), \
             patch.object(server, "build_source_layers", lambda *a, **k: None), \
             patch.object(server, "build_provenance", lambda dimensions: {}):
            request = DiagnosisRequest(domain=DOMAIN, company_name=COMPANY, competitors=competitor_text)
            return asyncio.run(server.diagnose(request))

    def test_no_competitors_means_no_competitor_part_and_no_matching(self):
        with patch.object(competitors, "compare_competitors", wraps=competitors.compare_competitors) as compare, \
             patch.object(server, "compare_competitors", wraps=competitors.compare_competitors):
            response = self._diagnose(None)
        self.assertIsNone(response.competitors)
        self.assertIsNone(response.competitor_comparison)
        compare.assert_not_called()
        self.assertEqual(len(self.perplexity_calls), 15)

    def test_competitors_are_recorded_and_counted(self):
        response = self._diagnose("HubSpot, Pipedrive (pipedrive.com)")
        self.assertEqual(response.competitors, [{"name": "HubSpot", "domain": ""}, {"name": "Pipedrive", "domain": "pipedrive.com"}])
        counts = {e["name"]: e["mentions"] for e in response.competitor_comparison["entries"]}
        self.assertEqual(counts, {"HubSpot": 10, COMPANY: 6, "Pipedrive": 3})
        self.assertEqual(len(self.perplexity_calls), 15)

    def test_changing_the_list_recounts_with_no_new_perplexity_calls(self):
        self._diagnose("HubSpot")
        self.assertEqual(len(self.perplexity_calls), 15)

        second = self._diagnose("Pipedrive (pipedrive.com), HubSpot")
        third = self._diagnose(None)
        again = self._diagnose("Pipedrive (pipedrive.com), HubSpot")

        self.assertEqual(len(self.perplexity_calls), 15)  # everything after the first run came from the stored answers
        self.assertEqual([e["name"] for e in second.competitor_comparison["entries"]], ["HubSpot", COMPANY, "Pipedrive"])
        self.assertIsNone(third.competitor_comparison)
        self.assertEqual(again.competitor_comparison, second.competitor_comparison)
        self.assertEqual(again.cached_at, second.cached_at)  # recounting does not make the diagnosis newer

    def test_same_input_twice_gives_identical_response(self):
        first = self._diagnose("HubSpot, Pipedrive")
        second = self._diagnose("HubSpot, Pipedrive")
        self.assertEqual(first.model_dump(), second.model_dump())
        self.assertEqual(len(self.perplexity_calls), 15)

    def test_too_many_competitors_is_rejected(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as caught:
            self._diagnose("A, B, C, D, E, F")
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
