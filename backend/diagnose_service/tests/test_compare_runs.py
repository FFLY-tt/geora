"""Tests for backend/scripts/compare_runs.py: two runs of one company, and whether the change is within the margin of error."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_BACKEND_DIR / "scripts"))

import compare_runs  # noqa: E402
from engines._stats import wilson_interval  # noqa: E402


def make_run(mentions, total=15, domain="close.com", gap_layer="community", method="v2", question_version=2, interval=True):
    details = {"mentions": mentions, "total": total}
    if interval:
        low, high = wilson_interval(mentions, total)
        details.update(ci_low=round(low, 3), ci_high=round(high, 3))
    return {
        "company_name": "Close",
        "domain": domain,
        "cached_at": "2026-10-16T09:00:00+00:00",
        "dimensions": [{"dimension": "citation_visibility", "score": round(100 * mentions / total), "details": details}],
        "source_layers": {
            "biggest_gap_layer": gap_layer,
            "conclusion": [f"AI leans on {gap_layer} sites and you are missing there."],
            "layers": [{"layer": gap_layer, "gap": 0.31}],
        },
        "provenance": {
            "method_versions": {"citation_visibility": method},
            "question_set": {"category_id": "crm", "version": question_version, "status": "frozen"},
        },
    }


def run_report(earlier, later):
    return compare_runs.report(compare_runs.summarize(earlier, "Earlier run"), compare_runs.summarize(later, "Later run"))


class CompareRunsTests(unittest.TestCase):
    def test_overlapping_ranges_are_within_the_margin_of_error(self):
        text = run_report(make_run(3), make_run(6))  # 20% -> 40%: ranges overlap with 15 answers
        self.assertIn("within the margin of error", text)
        self.assertIn("Mentioned in 3 of 15 answers (20%)", text)
        self.assertIn("Mentioned in 6 of 15 answers (40%)", text)
        self.assertIn("likely range", text)

    def test_non_overlapping_ranges_are_a_real_change(self):
        text = run_report(make_run(0), make_run(12))
        self.assertIn("rose by more than the margin of error", text)
        self.assertNotIn("within the margin of error", text)
        self.assertIn("fell by more than the margin of error", run_report(make_run(12), make_run(0)))

    def test_identical_counts_are_unchanged(self):
        self.assertIn("unchanged", run_report(make_run(5), make_run(5)))

    def test_biggest_gap_group_and_conclusion_are_listed_and_a_move_is_reported(self):
        text = run_report(make_run(5, gap_layer="community"), make_run(5, gap_layer="lists"))
        self.assertIn("Biggest gap: community (gap 31%)", text)
        self.assertIn("Biggest gap: lists (gap 31%)", text)
        self.assertIn("Top conclusion: AI leans on lists sites", text)
        self.assertIn("Biggest gap: moved from community to lists.", text)
        self.assertIn("Biggest gap: still community.", run_report(make_run(5), make_run(6)))

    def test_different_method_or_question_set_is_flagged(self):
        self.assertIn("Not directly comparable", run_report(make_run(5, method="v1"), make_run(5, method="v2")))
        self.assertIn("Not directly comparable", run_report(make_run(5, question_version=1), make_run(5, question_version=2)))
        self.assertNotIn("Not directly comparable", run_report(make_run(5), make_run(6)))

    def test_a_run_without_stored_interval_gets_one_computed(self):
        summary = compare_runs.summarize(make_run(3, interval=False), "Earlier run")
        low, high = wilson_interval(3, 15)
        self.assertEqual((summary["low"], summary["high"]), (low, high))

    def test_different_companies_cannot_be_compared(self):
        with self.assertRaises(compare_runs.CompareError):
            compare_runs.compare(
                compare_runs.summarize(make_run(3, domain="close.com"), "a"),
                compare_runs.summarize(make_run(3, domain="linear.app"), "b"),
            )

    def test_a_run_without_data_cannot_be_compared(self):
        empty = make_run(0)
        empty["dimensions"][0].update(score=-1, details={})
        with self.assertRaises(compare_runs.CompareError):
            compare_runs.summarize(empty, "Earlier run")

    def test_same_input_twice_gives_identical_report(self):
        self.assertEqual(run_report(make_run(3), make_run(6)), run_report(make_run(3), make_run(6)))

    def test_command_line_reads_files_and_a_cache_file_with_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second, cache = (Path(tmp) / name for name in ("a.json", "b.json", "cache.json"))
            first.write_text(json.dumps(make_run(3)), encoding="utf-8")
            second.write_text(json.dumps(make_run(6)), encoding="utf-8")
            cache.write_text(json.dumps({"close.com": make_run(6), "linear.app": make_run(1, domain="linear.app")}), encoding="utf-8")

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(compare_runs.main([str(first), str(second)]), 0)
            self.assertIn("within the margin of error", out.getvalue())

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(compare_runs.main([str(first), str(cache), "--domain", "close.com"]), 0)
            self.assertIn("Mentioned in 6 of 15 answers", out.getvalue())

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(compare_runs.main([str(first), str(cache)]), 2)
            self.assertIn("--domain", err.getvalue())


if __name__ == "__main__":
    unittest.main()
