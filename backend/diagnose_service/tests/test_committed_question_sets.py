"""The question sets committed under state/: every category has exactly 15 different buying questions."""

import itertools
import json
import sys
import unittest
from pathlib import Path

_SERVICE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVICE_DIR.parent))
sys.path.insert(0, str(_SERVICE_DIR))

import engines.citation_engine as citation_engine  # noqa: E402

_STATE = _SERVICE_DIR / "state"


def _current_sets():
    data = json.loads((_STATE / "question_sets.json").read_text(encoding="utf-8"))
    assignments = json.loads((_STATE / "company_categories.json").read_text(encoding="utf-8"))
    for category_id, record in data.items():
        current = next(v for v in record["versions"] if v["version"] == record["current_version"])
        brands = [
            {"company_name": a["company_name"], "domain": domain}
            for domain, a in assignments.items()
            if a["category_id"] == category_id
        ]
        yield category_id, current, brands


class CommittedQuestionSetTests(unittest.TestCase):
    def test_there_are_categories_to_check(self):
        self.assertGreaterEqual(len(list(_current_sets())), 5)

    def test_every_category_has_exactly_15_frozen_distinct_questions(self):
        for category_id, current, _brands in _current_sets():
            questions = current["questions"]
            self.assertEqual(len(questions), citation_engine.REQUIRED_QUERY_COUNT, category_id)
            self.assertEqual(len(set(questions)), len(questions), category_id)
            self.assertEqual(current["status"], "frozen", category_id)
            for a, b in itertools.combinations(questions, 2):
                self.assertIsNone(citation_engine.near_duplicate_of(a, [b]), f"{category_id}: {a!r} / {b!r}")

    def test_every_question_is_a_buying_question(self):
        for category_id, current, _brands in _current_sets():
            for question in current["questions"]:
                self.assertIsNone(citation_engine.buying_question_issue(question), f"{category_id}: {question!r}")

    def test_no_question_names_a_company_that_uses_the_set(self):
        for category_id, current, brands in _current_sets():
            for question in current["questions"]:
                blocking, _format = citation_engine._deterministic_question_issues(question, brands)
                self.assertEqual(blocking, [], f"{category_id}: {question!r}")

    def test_no_question_names_any_known_product(self):
        for category_id, current, brands in _current_sets():
            for question in current["questions"]:
                extra = [b["company_name"] for b in brands]
                self.assertEqual(citation_engine.product_names_in_question(question, extra), [], f"{category_id}: {question!r}")

    def test_the_set_was_made_with_the_current_method(self):
        for category_id, current, _brands in _current_sets():
            self.assertEqual(current["generation_method_version"], citation_engine.QUESTION_SET_METHOD_VERSION, category_id)


if __name__ == "__main__":
    unittest.main()
