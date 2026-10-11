"""
Tests for backend/scripts/stability_audit.py's core logic.

Every network call and every engine check_* call is mocked: no API key
is required, and none of this ever touches the network, so it runs
the same locally and in CI. Lives under diagnose_service/tests (the
directory CI already discovers) even though the module under test is
in backend/scripts/, the same convention test_batch_diagnose.py uses.
"""

import csv  # noqa: F401 - re-exported for parity with other test files' import style
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

_THIS_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _THIS_DIR.parent  # backend/diagnose_service
_BACKEND_DIR = _SERVICE_DIR.parent  # backend/
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"

sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR))

import stability_audit  # noqa: E402


class WilsonIntervalTests(unittest.TestCase):
    def test_zero_total_returns_none(self):
        self.assertIsNone(stability_audit.wilson_interval(0, 0))

    def test_all_successes_interval_is_below_one(self):
        lower, upper = stability_audit.wilson_interval(10, 10)
        self.assertGreater(lower, 0.6)
        self.assertLessEqual(upper, 1.0)

    def test_all_failures_interval_is_above_zero_lower_bound(self):
        lower, upper = stability_audit.wilson_interval(0, 10)
        self.assertEqual(lower, 0.0)
        self.assertLess(upper, 0.4)

    def test_half_and_half_interval_is_centered_near_half(self):
        lower, upper = stability_audit.wilson_interval(5, 10)
        self.assertLess(lower, 0.5)
        self.assertGreater(upper, 0.5)

    def test_more_data_narrows_the_interval(self):
        small_lower, small_upper = stability_audit.wilson_interval(5, 10)
        big_lower, big_upper = stability_audit.wilson_interval(50, 100)
        self.assertLess(big_upper - big_lower, small_upper - small_lower)


class Sha256Tests(unittest.TestCase):
    def test_deterministic_and_64_hex_chars(self):
        first = stability_audit._sha256("hello")
        second = stability_audit._sha256("hello")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_different_input_different_hash(self):
        self.assertNotEqual(stability_audit._sha256("a"), stability_audit._sha256("b"))

    def test_empty_or_none_does_not_raise(self):
        self.assertEqual(len(stability_audit._sha256("")), 64)
        self.assertEqual(len(stability_audit._sha256(None)), 64)


class LoggingWrapperTests(unittest.TestCase):
    """
    Tests _logging_get/_logging_post directly against a fake
    "real" requests.get/post, never touching the actual global
    requests module — see this module's docstring on test isolation.
    """

    def setUp(self):
        stability_audit._call_log.clear()

    def test_logging_get_records_url_hash_and_length(self):
        fake_response = MagicMock(status_code=200, text="hello world", url="https://example.com/")
        with patch.object(stability_audit, "_real_requests_get", return_value=fake_response):
            response = stability_audit._logging_get("https://example.com/")

        self.assertIs(response, fake_response)
        self.assertEqual(len(stability_audit._call_log), 1)
        entry = stability_audit._call_log[0]
        self.assertEqual(entry["kind"], "http_get")
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["content_length"], len("hello world"))
        self.assertEqual(entry["content_sha256"], stability_audit._sha256("hello world"))

    def test_logging_get_records_failure_and_still_raises(self):
        with patch.object(stability_audit, "_real_requests_get", side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(requests.ConnectionError):
                stability_audit._logging_get("https://example.com/")

        self.assertEqual(len(stability_audit._call_log), 1)
        self.assertEqual(stability_audit._call_log[0]["status"], "error")

    def test_logging_post_records_temperature_input_and_output_hash(self):
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"choices": [{"message": {"content": "the answer"}}]}
        payload = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}], "temperature": 0}

        with patch.object(stability_audit, "_real_requests_post", return_value=fake_response):
            stability_audit._logging_post("https://api.deepseek.com/v1/chat/completions", json=payload)

        entry = stability_audit._call_log[0]
        self.assertEqual(entry["kind"], "http_post")
        self.assertEqual(entry["provider"], "deepseek")
        self.assertEqual(entry["temperature"], 0)
        self.assertEqual(entry["output_sha256"], stability_audit._sha256("the answer"))
        self.assertIn("the answer", entry["output_snippet"])

    def test_logging_post_with_no_temperature_records_none(self):
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"choices": [{"message": {"content": "x"}}]}
        payload = {"model": "sonar", "messages": [{"role": "user", "content": "hi"}]}

        with patch.object(stability_audit, "_real_requests_post", return_value=fake_response):
            stability_audit._logging_post("https://api.perplexity.ai/chat/completions", json=payload)

        entry = stability_audit._call_log[0]
        self.assertIsNone(entry["temperature"])
        self.assertEqual(entry["provider"], "perplexity")


def _get_call(url: str, status: str = "ok", content_sha256: str = "aaa", content_length: int = 100) -> dict:
    if status == "error":
        return {"kind": "http_get", "url": url, "status": "error", "error": "boom"}
    return {
        "kind": "http_get",
        "url": url,
        "status": "ok",
        "status_code": 200,
        "final_url": url,
        "content_length": content_length,
        "content_sha256": content_sha256,
    }


def _post_call(input_sha256: str, output_sha256: str, provider: str = "deepseek", temperature=0) -> dict:
    return {
        "kind": "http_post",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "status": "ok",
        "status_code": 200,
        "provider": provider,
        "temperature": temperature,
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
        "output_snippet": output_sha256,
    }


class AnalyzePagesReadTests(unittest.TestCase):
    def test_identical_urls_and_hashes_across_runs(self):
        per_run = [[_get_call("https://a.com", content_sha256="h1")] for _ in range(3)]

        result = stability_audit._analyze_pages_read(per_run)

        self.assertTrue(result["urls_identical"])
        self.assertEqual(result["content_changes"], {})
        self.assertFalse(result["any_fetch_failures"])

    def test_content_hash_change_is_detected(self):
        per_run = [
            [_get_call("https://a.com", content_sha256="h1")],
            [_get_call("https://a.com", content_sha256="h1")],
            [_get_call("https://a.com", content_sha256="h2")],
        ]

        result = stability_audit._analyze_pages_read(per_run)

        self.assertTrue(result["urls_identical"])
        self.assertIn("https://a.com", result["content_changes"])

    def test_different_url_sets_detected(self):
        per_run = [
            [_get_call("https://a.com")],
            [_get_call("https://a.com"), _get_call("https://b.com")],
        ]

        result = stability_audit._analyze_pages_read(per_run)

        self.assertFalse(result["urls_identical"])

    def test_fetch_failure_detected(self):
        per_run = [[_get_call("https://a.com")], [_get_call("https://a.com", status="error")]]

        result = stability_audit._analyze_pages_read(per_run)

        self.assertTrue(result["any_fetch_failures"])


class AnalyzeLlmCallsTests(unittest.TestCase):
    def test_same_input_different_output_is_noisy(self):
        per_run = [
            [_post_call(input_sha256="in1", output_sha256="out1")],
            [_post_call(input_sha256="in1", output_sha256="out2")],
        ]

        result = stability_audit._analyze_llm_calls(per_run)

        self.assertEqual(result["repeated_inputs_count"], 1)
        self.assertIn("in1", result["noisy_inputs"])

    def test_same_input_same_output_is_not_noisy(self):
        per_run = [
            [_post_call(input_sha256="in1", output_sha256="out1")],
            [_post_call(input_sha256="in1", output_sha256="out1")],
        ]

        result = stability_audit._analyze_llm_calls(per_run)

        self.assertEqual(result["repeated_inputs_count"], 1)
        self.assertEqual(result["noisy_inputs"], {})

    def test_no_repeated_inputs(self):
        per_run = [
            [_post_call(input_sha256="in1", output_sha256="out1")],
            [_post_call(input_sha256="in2", output_sha256="out2")],
        ]

        result = stability_audit._analyze_llm_calls(per_run)

        self.assertEqual(result["repeated_inputs_count"], 0)

    def test_no_calls_at_all(self):
        result = stability_audit._analyze_llm_calls([[], []])

        self.assertEqual(result["total_calls"], 0)


class CitationMentionTotalsTests(unittest.TestCase):
    def test_counts_hits_and_excludes_uncertain_and_failed_runs(self):
        details = {
            "per_query_results": [
                {
                    "runs": [
                        {"status": "ok", "mention_classification": "hit", "is_hit": True},
                        {"status": "ok", "mention_classification": "no_mention", "is_hit": False},
                        {"status": "ok", "mention_classification": "uncertain", "is_hit": None},
                        {"status": "error"},
                    ]
                }
            ]
        }

        mentions, total = stability_audit._citation_mention_totals(details)

        self.assertEqual(mentions, 1)
        self.assertEqual(total, 2)


class BuildReportIntegrationTests(unittest.TestCase):
    def _fake_dimensions(self, citation_score: int, other_score: int = 50) -> dict:
        citation_details = {
            "category": "test category",
            "generation_log": [{"outcome": "cache_hit"}],
            "per_query_results": [
                {
                    "query": "q1",
                    "runs": [{"status": "ok", "mention_classification": "hit", "is_hit": True}],
                }
            ],
        }
        return {
            "citation_visibility": {"score": citation_score, "details": citation_details, "http_calls": []},
            "topic_relevance": {"score": other_score, "details": {}, "http_calls": []},
            "evidence_density": {"score": other_score, "details": {}, "http_calls": []},
            "content_recency": {"score": other_score, "details": {}, "http_calls": []},
            "fact_consistency": {"score": other_score, "details": {}, "http_calls": []},
            "technical_readability": {"score": other_score, "details": {}, "http_calls": []},
        }

    def test_stable_dimension_reports_no_further_analysis(self):
        records = [
            {"run": i, "dimensions": self._fake_dimensions(citation_score=40, other_score=50)} for i in range(1, 4)
        ]

        report = stability_audit.build_report("Acme", "acme.com", records)

        self.assertIn("### evidence_density", report)
        self.assertIn("Stable; no further analysis.", report)
        self.assertNotIn("Step-by-step divergence for `evidence_density`", report)

    def test_unstable_dimension_triggers_divergence_section(self):
        records = [
            {"run": 1, "dimensions": self._fake_dimensions(citation_score=40, other_score=10)},
            {"run": 2, "dimensions": self._fake_dimensions(citation_score=40, other_score=50)},
            {"run": 3, "dimensions": self._fake_dimensions(citation_score=40, other_score=90)},
        ]

        report = stability_audit.build_report("Acme", "acme.com", records)

        self.assertIn("Step-by-step divergence for `fact_consistency`", report)
        self.assertIn("Range = 80", report)

    def test_report_includes_mention_rate_and_question_sections(self):
        records = [
            {"run": i, "dimensions": self._fake_dimensions(citation_score=40, other_score=50)} for i in range(1, 4)
        ]

        report = stability_audit.build_report("Acme", "acme.com", records)

        self.assertIn("Wilson 95% confidence interval", report)
        self.assertIn("Buyer questions used for citation_visibility", report)
        self.assertIn("Same question set was used in **all 3 runs**", report)


class MainEndToEndTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self._tmp_dir.name) / "output" / "stability"
        self._output_patch = patch.object(stability_audit, "OUTPUT_DIR", self.output_dir)
        self._output_patch.start()
        stability_audit._call_log.clear()

    def tearDown(self):
        self._output_patch.stop()
        self._tmp_dir.cleanup()

    def _fake_dimension(self, dimension: str, score: int) -> MagicMock:
        return MagicMock(score=score, details={
            "category": "cat",
            "generation_log": [{"outcome": "cache_hit"}],
            "per_query_results": [],
        })

    def test_runs_requested_number_of_times_and_writes_outputs(self):
        engine_patches = [
            patch.object(stability_audit.citation_engine, "check_citation_visibility",
                          return_value=self._fake_dimension("citation_visibility", 40)),
            patch.object(stability_audit.evidence_engine, "check_evidence_density",
                          return_value=self._fake_dimension("evidence_density", 50)),
            patch.object(stability_audit.recency_engine, "check_recency",
                          return_value=self._fake_dimension("content_recency", 60)),
            patch.object(stability_audit.fact_engine, "check_fact_consistency",
                          return_value=self._fake_dimension("fact_consistency", 70)),
            patch.object(stability_audit.technical_engine, "check_technical_readability",
                          return_value=self._fake_dimension("technical_readability", 80)),
            patch.object(stability_audit.relevance_engine, "check_topic_relevance",
                          return_value=self._fake_dimension("topic_relevance", 90)),
        ]
        for p in engine_patches:
            p.start()
        sleep_patch = patch.object(stability_audit.time, "sleep")
        sleep_patch.start()
        argv_patch = patch.object(sys, "argv", ["stability_audit.py", "Acme", "acme.com", "--runs", "3", "--yes"])
        argv_patch.start()
        try:
            stability_audit.main()
        finally:
            argv_patch.stop()
            sleep_patch.stop()
            for p in engine_patches:
                p.stop()

        results_file = self.output_dir / "acme.com_runs.jsonl"
        report_file = self.output_dir / "report_acme.com.md"
        self.assertTrue(results_file.exists())
        self.assertTrue(report_file.exists())

        with open(results_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 3)
        self.assertEqual([r["run"] for r in lines], [1, 2, 3])

        report_text = report_file.read_text(encoding="utf-8")
        self.assertIn("Stability audit: Acme (acme.com)", report_text)
        self.assertIn("Runs: 3", report_text)

    def test_declining_confirmation_does_not_run_anything(self):
        engine_patch = patch.object(stability_audit.citation_engine, "check_citation_visibility")
        mock_citation = engine_patch.start()
        with patch("builtins.input", return_value="n"), \
             patch.object(sys, "argv", ["stability_audit.py", "Acme", "acme.com", "--runs", "3"]):
            try:
                stability_audit.main()
            finally:
                engine_patch.stop()

        mock_citation.assert_not_called()
        self.assertFalse((self.output_dir / "acme.com_runs.jsonl").exists())

    def test_no_judgment_cache_flag_is_noted_in_the_report(self):
        engine_patches = [
            patch.object(stability_audit.citation_engine, "check_citation_visibility",
                          return_value=self._fake_dimension("citation_visibility", 40)),
            patch.object(stability_audit.evidence_engine, "check_evidence_density",
                          return_value=self._fake_dimension("evidence_density", 50)),
            patch.object(stability_audit.recency_engine, "check_recency",
                          return_value=self._fake_dimension("content_recency", 60)),
            patch.object(stability_audit.fact_engine, "check_fact_consistency",
                          return_value=self._fake_dimension("fact_consistency", 70)),
            patch.object(stability_audit.technical_engine, "check_technical_readability",
                          return_value=self._fake_dimension("technical_readability", 80)),
            patch.object(stability_audit.relevance_engine, "check_topic_relevance",
                          return_value=self._fake_dimension("topic_relevance", 90)),
        ]
        for p in engine_patches:
            p.start()
        sleep_patch = patch.object(stability_audit.time, "sleep")
        sleep_patch.start()
        argv_patch = patch.object(
            sys, "argv", ["stability_audit.py", "Acme", "acme.com", "--runs", "2", "--yes", "--no-judgment-cache"]
        )
        argv_patch.start()
        try:
            stability_audit.main()
        finally:
            argv_patch.stop()
            sleep_patch.stop()
            for p in engine_patches:
                p.stop()

        # The bypass patchers must be cleaned up afterwards, not left installed.
        import engines._judgment_cache as judgment_cache_module

        self.assertIs(stability_audit.evidence_engine.get_cached_judgment, judgment_cache_module.get_cached_judgment)

        report_text = (self.output_dir / "report_acme.com.md").read_text(encoding="utf-8")
        self.assertIn("bypassed", report_text)


class JudgmentCacheBypassTests(unittest.TestCase):
    def test_install_forces_miss_and_noop_save_then_restores_originals(self):
        original_evidence_get = stability_audit.evidence_engine.get_cached_judgment
        original_evidence_save = stability_audit.evidence_engine.save_judgment
        original_relevance_get = stability_audit.relevance_engine.get_cached_judgment
        original_relevance_save = stability_audit.relevance_engine.save_judgment

        patchers = stability_audit._install_judgment_cache_bypass()
        try:
            self.assertIsNone(stability_audit.evidence_engine.get_cached_judgment("any-key"))
            self.assertIsNone(stability_audit.relevance_engine.get_cached_judgment("any-key"))
            # Must not raise, and must not make the next get_cached_judgment call return a hit.
            stability_audit.evidence_engine.save_judgment("any-key", {"score": 99})
            self.assertIsNone(stability_audit.evidence_engine.get_cached_judgment("any-key"))
        finally:
            for p in patchers:
                p.stop()

        self.assertIs(stability_audit.evidence_engine.get_cached_judgment, original_evidence_get)
        self.assertIs(stability_audit.evidence_engine.save_judgment, original_evidence_save)
        self.assertIs(stability_audit.relevance_engine.get_cached_judgment, original_relevance_get)
        self.assertIs(stability_audit.relevance_engine.save_judgment, original_relevance_save)


class EvidenceDensityCategoryTableTests(unittest.TestCase):
    def test_table_includes_category_counts_per_run(self):
        records = [
            {
                "run": 1,
                "dimensions": {
                    "evidence_density": {
                        "score": 40,
                        "details": {"counts_by_category": {"number_or_statistic": 2, "attributed_quote": 0}},
                    }
                },
            },
            {
                "run": 2,
                "dimensions": {
                    "evidence_density": {
                        "score": 55,
                        "details": {"counts_by_category": {"number_or_statistic": 3, "attributed_quote": 1}},
                    }
                },
            },
        ]

        table = stability_audit._evidence_density_category_table(records)

        self.assertIn("number_or_statistic", table)
        self.assertIn("attributed_quote", table)
        self.assertIn("| 1 | 2 | 0 | 40 |", table)
        self.assertIn("| 2 | 3 | 1 | 55 |", table)

    def test_empty_when_no_run_has_the_breakdown(self):
        records = [{"run": 1, "dimensions": {"evidence_density": {"score": -1, "details": {}}}}]

        table = stability_audit._evidence_density_category_table(records)

        self.assertEqual(table, "")


if __name__ == "__main__":
    unittest.main()
