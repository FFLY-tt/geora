"""
Tests for backend/scripts/batch_diagnose.py.

Every network call (robots.txt fetch, homepage fetch for redirect
detection) and every engine check_* call is mocked: no API key is
required, and none of this ever touches the network, so it runs the
same locally and in CI. Lives under diagnose_service/tests (the
directory CI already discovers) even though the module under test is
in backend/scripts/, the same way test_fetch_utils.py here tests
backend/shared/_fetch_utils.py.
"""

import csv
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_THIS_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _THIS_DIR.parent  # backend/diagnose_service
_BACKEND_DIR = _SERVICE_DIR.parent  # backend/
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"

sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR))

import batch_diagnose  # noqa: E402


def _fake_dimension(dimension: str, score: int, details: dict | None = None) -> MagicMock:
    fake = MagicMock()
    fake.model_dump.return_value = {"dimension": dimension, "score": score, "details": details or {}}
    return fake


def _patch_all_engines(citation_side_effect=None):
    """
    Patch all 6 engine check_* functions so _run_full_diagnosis never
    touches the network. If `citation_side_effect` is given, it's used
    as check_citation_visibility's side_effect (e.g. to simulate one
    company raising); otherwise it returns a canned "ok" result.

    Returns (patchers, mocks): `patchers` is the list to .start()/.stop();
    `mocks` is a dict of the mock objects themselves, keyed by engine
    name, so a test can still assert on calls after the patchers are
    stopped (stopping restores the original function on the module).
    """
    citation_mock = MagicMock(
        side_effect=citation_side_effect,
        return_value=None if citation_side_effect else _fake_dimension("citation_visibility", 40),
    )
    evidence_mock = MagicMock(return_value=_fake_dimension("evidence_density", 50))
    recency_mock = MagicMock(return_value=_fake_dimension("content_recency", 60))
    fact_mock = MagicMock(return_value=_fake_dimension("fact_consistency", 70))
    technical_mock = MagicMock(return_value=_fake_dimension("technical_readability", 80))
    relevance_mock = MagicMock(return_value=_fake_dimension("topic_relevance", 90))

    patchers = [
        patch.object(batch_diagnose.citation_engine, "check_citation_visibility", citation_mock),
        patch.object(batch_diagnose.evidence_engine, "check_evidence_density", evidence_mock),
        patch.object(batch_diagnose.recency_engine, "check_recency", recency_mock),
        patch.object(batch_diagnose.fact_engine, "check_fact_consistency", fact_mock),
        patch.object(batch_diagnose.technical_engine, "check_technical_readability", technical_mock),
        patch.object(batch_diagnose.relevance_engine, "check_topic_relevance", relevance_mock),
    ]
    mocks = {
        "citation": citation_mock,
        "evidence": evidence_mock,
        "recency": recency_mock,
        "fact": fact_mock,
        "technical": technical_mock,
        "relevance": relevance_mock,
    }
    return patchers, mocks


def _fake_requests_get(robots_disallowed_domains: frozenset[str] = frozenset(), redirect_to: dict | None = None):
    """
    A requests.get stand-in covering both call sites in batch_diagnose:
    the robots.txt fetch and the homepage fetch used for redirect
    detection. `redirect_to` maps an original domain to the full URL it
    should appear to land on.
    """
    redirect_to = redirect_to or {}

    def _get(url, headers=None, timeout=None):
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()

        if url.endswith("/robots.txt"):
            host = url.split("//", 1)[-1].split("/", 1)[0]
            if host in robots_disallowed_domains:
                response.text = "User-agent: *\nDisallow: /\n"
            else:
                response.text = ""
            return response

        # Homepage fetch for redirect detection.
        for domain, landed_url in redirect_to.items():
            if domain in url:
                response.url = landed_url
                response.text = ""
                return response
        response.url = url
        response.text = ""
        return response

    return _get


def _read_jsonl(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class BatchDiagnoseTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp_dir = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp_dir.name)
        self.output_dir = tmp_path / "output"
        self.results_file = self.output_dir / "results.jsonl"
        self.summary_file = self.output_dir / "summary.csv"
        self.csv_path = tmp_path / "companies.csv"

        self._patches = [
            patch.object(batch_diagnose, "OUTPUT_DIR", self.output_dir),
            patch.object(batch_diagnose, "RESULTS_FILE", self.results_file),
            patch.object(batch_diagnose, "SUMMARY_FILE", self.summary_file),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp_dir.cleanup()

    def _write_companies_csv(self, rows: list[tuple[str, str]]) -> None:
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["company_name", "domain"])
            writer.writerows(rows)

    def _run_main(self):
        with patch.object(sys, "argv", ["batch_diagnose.py", str(self.csv_path), "--yes"]):
            batch_diagnose.main()

    def test_resume_skips_companies_that_already_have_a_result(self):
        self._write_companies_csv([("Close", "close.com"), ("Linear", "linear.app")])
        self.output_dir.mkdir(parents=True)
        with open(self.results_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"company_name": "Close", "domain": "close.com", "status": "ok"}) + "\n")

        engine_patches, mocks = _patch_all_engines()
        for p in engine_patches:
            p.start()
        requests_get_patch = patch.object(batch_diagnose.requests, "get", side_effect=_fake_requests_get())
        requests_get_patch.start()
        sleep_patch = patch.object(batch_diagnose.time, "sleep")
        sleep_patch.start()
        try:
            self._run_main()
            mocks["citation"].assert_called_once()
            called_domain = mocks["citation"].call_args[0][1]
            self.assertEqual(called_domain, "linear.app")
        finally:
            requests_get_patch.stop()
            sleep_patch.stop()
            for p in engine_patches:
                p.stop()

        records = _read_jsonl(self.results_file)
        domains = {r["domain"] for r in records}
        self.assertEqual(domains, {"close.com", "linear.app"})

    def test_one_company_failing_does_not_stop_the_batch(self):
        self._write_companies_csv([("Close", "close.com"), ("Linear", "linear.app")])

        def citation_side_effect(company_name, domain):
            if domain == "close.com":
                raise RuntimeError("simulated engine failure")
            return _fake_dimension("citation_visibility", 40)

        engine_patches, _mocks = _patch_all_engines(citation_side_effect=citation_side_effect)
        for p in engine_patches:
            p.start()
        requests_get_patch = patch.object(batch_diagnose.requests, "get", side_effect=_fake_requests_get())
        requests_get_patch.start()
        sleep_patch = patch.object(batch_diagnose.time, "sleep")
        sleep_patch.start()
        try:
            self._run_main()
        finally:
            requests_get_patch.stop()
            sleep_patch.stop()
            for p in engine_patches:
                p.stop()

        records = {r["domain"]: r for r in _read_jsonl(self.results_file)}
        self.assertEqual(records["close.com"]["status"], "error")
        self.assertIn("simulated engine failure", records["close.com"]["error"])
        self.assertEqual(records["linear.app"]["status"], "ok")
        self.assertIsNotNone(records["linear.app"]["dimensions"])

        with open(self.summary_file, "r", encoding="utf-8") as f:
            summary_rows = {row["domain"]: row for row in csv.DictReader(f)}
        self.assertEqual(summary_rows["close.com"]["status"], "error")
        self.assertEqual(summary_rows["close.com"]["error"], "simulated engine failure")
        self.assertEqual(summary_rows["linear.app"]["status"], "ok")

    def test_robots_disallowed_skips_without_running_any_engine(self):
        self._write_companies_csv([("Close", "close.com")])

        engine_patches, mocks = _patch_all_engines()
        for p in engine_patches:
            p.start()
        requests_get_patch = patch.object(
            batch_diagnose.requests, "get",
            side_effect=_fake_requests_get(robots_disallowed_domains=frozenset({"close.com"})),
        )
        requests_get_patch.start()
        sleep_patch = patch.object(batch_diagnose.time, "sleep")
        sleep_patch.start()
        try:
            self._run_main()
            mocks["citation"].assert_not_called()
            mocks["evidence"].assert_not_called()
        finally:
            requests_get_patch.stop()
            sleep_patch.stop()
            for p in engine_patches:
                p.stop()

        records = _read_jsonl(self.results_file)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "skipped_robots")
        self.assertIsNone(records[0]["dimensions"])

    def test_redirect_is_recorded_but_diagnosis_still_runs(self):
        self._write_companies_csv([("Acme", "acme.com")])

        engine_patches, mocks = _patch_all_engines()
        for p in engine_patches:
            p.start()
        requests_get_patch = patch.object(
            batch_diagnose.requests, "get",
            side_effect=_fake_requests_get(redirect_to={"acme.com": "https://acquirer.com/"}),
        )
        requests_get_patch.start()
        sleep_patch = patch.object(batch_diagnose.time, "sleep")
        sleep_patch.start()
        try:
            self._run_main()
            mocks["citation"].assert_called_once()
        finally:
            requests_get_patch.stop()
            sleep_patch.stop()
            for p in engine_patches:
                p.stop()

        records = _read_jsonl(self.results_file)
        self.assertEqual(records[0]["status"], "redirected")
        self.assertEqual(records[0]["redirected_domain"], "acquirer.com")
        self.assertIsNotNone(records[0]["dimensions"])


if __name__ == "__main__":
    unittest.main()
