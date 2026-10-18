"""
Inspect the frozen buyer question sets in
backend/diagnose_service/state/, and create a NEW version of one
category's set when explicitly asked to.

Question sets never expire and are never regenerated implicitly (see
diagnose_service/engines/_question_sets.py). This script is the only
way to replace one: `regenerate` generates and auto-checks a new set
from the category's stored description, appends it as the next version,
and makes it current. Every older version stays in the file. Companies
in the category are measured with the new version from their next
diagnosis on, and the frontend will not compare those scores against
results from the old version.

Usage:
    python backend/scripts/question_sets.py list
    python backend/scripts/question_sets.py show <category_id>
    python backend/scripts/question_sets.py regenerate <category_id>
    python backend/scripts/question_sets.py extend <category_id>
    python backend/scripts/question_sets.py extend-all

`extend` grows a category's current set to the required number of questions
(15) by keeping its questions as they are and generating only the missing
ones, which are checked like any new question and must not repeat or reword
the kept ones. It adds a new version; the older, smaller version is kept.
`extend-all` does that for every category whose current set is too small.

After `regenerate` or `extend`, commit and push the change to state/.
"""

import argparse
import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
_SERVICE_DIR = _BACKEND_DIR / "diagnose_service"

sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))

from engines import _question_sets  # noqa: E402
from engines import citation_engine  # noqa: E402
from shared.config import settings  # noqa: E402


def _list() -> int:
    for category in _question_sets.list_categories():
        current = _question_sets.get_current_question_set(category["category_id"])
        companies = _question_sets.get_companies_in_category(category["category_id"])
        status = f'v{current["version"]} {current["status"]}' if current else "no current version"
        domains = ", ".join(c["domain"] for c in companies) or "-"
        print(f'{category["category_id"]}  [{status}]  {category["category_name"]}  companies: {domains}')
    return 0


def _show(category_id: str) -> int:
    record = _question_sets.get_category(category_id)
    if record is None:
        print(f"Unknown category: {category_id}", file=sys.stderr)
        return 1
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


def _regenerate(category_id: str) -> int:
    record = _question_sets.get_category(category_id)
    if record is None:
        print(f"Unknown category: {category_id}", file=sys.stderr)
        return 1
    if not settings.has_deepseek_key():
        print("DEEPSEEK_API_KEY is not configured.", file=sys.stderr)
        return 1

    brand_terms = _question_sets.get_companies_in_category(category_id)
    version_record, attempts = citation_engine.generate_checked_question_set(
        record["category_description"], brand_terms
    )
    if version_record is None:
        print(
            f"No usable question set after {len(attempts)} attempts; nothing was written.\n"
            + json.dumps(attempts, indent=2, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1

    new_version = _question_sets.add_question_set_version(category_id, version_record)
    print(f"{category_id}: created version {new_version} ({version_record['status']})")
    for question in version_record["questions"]:
        print(f"  - {question}")
    print("Commit and push backend/diagnose_service/state/ to share it.")
    return 0


def _extend(category_id: str) -> int:
    record = _question_sets.get_category(category_id)
    if record is None:
        print(f"Unknown category: {category_id}", file=sys.stderr)
        return 1
    if not settings.has_deepseek_key():
        print("DEEPSEEK_API_KEY is not configured.", file=sys.stderr)
        return 1
    current = _question_sets.get_current_question_set(category_id)
    kept = list(current["questions"])
    required = citation_engine.REQUIRED_QUERY_COUNT
    if len(kept) >= required:
        print(f"{category_id}: already has {len(kept)} questions, nothing to do")
        return 0

    brand_terms = _question_sets.get_companies_in_category(category_id)
    version_record, attempts = citation_engine.generate_checked_question_set(
        record["category_description"], brand_terms, existing_questions=kept
    )
    if version_record is None:
        print(
            f"{category_id}: no usable batch after {len(attempts)} attempts; nothing was written.\n"
            + json.dumps(attempts, indent=2, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1

    version_record["extends_version"] = current["version"]
    new_version = _question_sets.add_question_set_version(category_id, version_record)
    print(f"{category_id}: version {current['version']} ({len(kept)} questions) -> version {new_version} "
          f"({len(version_record['questions'])} questions, {version_record['status']})")
    for question in version_record["questions"][len(kept):]:
        print(f"  + {question}")
    return 0


def _extend_all() -> int:
    status = 0
    for category in _question_sets.list_categories():
        status |= _extend(category["category_id"])
    print("Commit and push backend/diagnose_service/state/ to share it.")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or explicitly regenerate frozen buyer question sets.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List every category with its current version and companies.")
    show_parser = subparsers.add_parser("show", help="Print a category's full record, all versions.")
    show_parser.add_argument("category_id")
    regenerate_parser = subparsers.add_parser(
        "regenerate", help="Generate a new version of a category's question set; old versions are kept."
    )
    regenerate_parser.add_argument("category_id")
    extend_parser = subparsers.add_parser(
        "extend", help="Grow a category's set to the required size, keeping its existing questions."
    )
    extend_parser.add_argument("category_id")
    subparsers.add_parser("extend-all", help="Extend every category whose current set is too small.")
    args = parser.parse_args()

    if args.command == "list":
        return _list()
    if args.command == "show":
        return _show(args.category_id)
    if args.command == "extend":
        return _extend(args.category_id)
    if args.command == "extend-all":
        return _extend_all()
    return _regenerate(args.category_id)


if __name__ == "__main__":
    sys.exit(main())
