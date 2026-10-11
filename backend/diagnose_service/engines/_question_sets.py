"""
Frozen buyer question sets, one per PRODUCT CATEGORY, plus which
category each company (domain) belongs to. Both live in the shared,
committed state directory (see engines._state_store):

- state/question_sets.json: {category_id: {category_name,
  category_description, current_version, versions: [...]}}. Each
  version records its 5 questions, version number, generation date,
  status ("frozen" or "needs_attention"), and the automatic check
  results with per-question reasons.
- state/company_categories.json: {domain: {category_id, company_name,
  assigned_on, classified_as, match_decision, homepage_text_sha256}}.

Why per category and not per domain: two companies selling the same
kind of product are measured against the exact same 5 buyer questions,
so their citation_visibility / topic_relevance scores are directly
comparable. Why frozen: a question set never expires and is never
regenerated implicitly (not by time, not by switching machines). A new
version is only ever created explicitly, via
backend/scripts/question_sets.py; older versions stay in the file.
"""

import re
from pathlib import Path

from engines import _state_store


def _question_sets_file() -> Path:
    return _state_store.STATE_DIR / "question_sets.json"


def _company_categories_file() -> Path:
    return _state_store.STATE_DIR / "company_categories.json"


def normalize_domain(domain: str) -> str:
    """'https://www.Example.com/' -> 'example.com', so one company never gets two assignments."""
    value = domain.strip().lower()
    value = re.sub(r"^[a-z]+://", "", value)
    value = value.split("/")[0]
    if value.startswith("www."):
        value = value[len("www."):]
    return value


def slugify_category_name(name: str) -> str:
    """A stable, file-friendly category id derived from the category name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "category"


def get_company_assignment(domain: str) -> dict | None:
    """The stored category assignment for `domain`, or None if this company has never been classified."""
    entry = _state_store.read_state(_company_categories_file()).get(normalize_domain(domain))
    return entry if isinstance(entry, dict) else None


def list_categories() -> list[dict]:
    """Every known category as {category_id, category_name, category_description}, sorted by id."""
    question_sets = _state_store.read_state(_question_sets_file())
    return [
        {
            "category_id": category_id,
            "category_name": record.get("category_name", ""),
            "category_description": record.get("category_description", ""),
        }
        for category_id, record in sorted(question_sets.items())
    ]


def get_category(category_id: str) -> dict | None:
    """The full stored record for `category_id` (all versions), or None."""
    record = _state_store.read_state(_question_sets_file()).get(category_id)
    return record if isinstance(record, dict) else None


def get_current_question_set(category_id: str) -> dict | None:
    """
    The category's current version merged with its name/description:
    {category_id, category_name, category_description, version,
    questions, status, generated_on, ...}. None if the category or its
    current version is missing.
    """
    record = get_category(category_id)
    if record is None:
        return None
    current = record.get("current_version")
    version = next((v for v in record.get("versions", []) if v.get("version") == current), None)
    if version is None:
        return None
    return {
        "category_id": category_id,
        "category_name": record.get("category_name", ""),
        "category_description": record.get("category_description", ""),
        **version,
    }


def get_companies_in_category(category_id: str) -> list[dict]:
    """[{domain, company_name}] for every company assigned to `category_id`, sorted by domain."""
    assignments = _state_store.read_state(_company_categories_file())
    return [
        {"domain": domain, "company_name": entry.get("company_name", "")}
        for domain, entry in sorted(assignments.items())
        if isinstance(entry, dict) and entry.get("category_id") == category_id
    ]


def save_company_assignment(domain: str, assignment: dict) -> None:
    """Store `domain`'s category assignment. An existing assignment is never overwritten."""
    key = normalize_domain(domain)

    def _mutate(assignments: dict) -> None:
        assignments.setdefault(key, assignment)

    _state_store.update_state(_company_categories_file(), _mutate)


def create_category(category_name: str, category_description: str, version_record: dict) -> str:
    """
    Store a brand-new category with `version_record` as its version 1,
    and return the category id actually used. If a category with the
    exact same description already exists (e.g. a concurrent diagnosis
    of a similar company got there first), that one is reused and
    nothing is written. Otherwise an id collision on the slug gets a
    numeric suffix.
    """

    def _mutate(question_sets: dict) -> str:
        for existing_id, record in question_sets.items():
            if record.get("category_description") == category_description:
                return existing_id
        base_id = slugify_category_name(category_name)
        category_id = base_id
        suffix = 2
        while category_id in question_sets:
            category_id = f"{base_id}-{suffix}"
            suffix += 1
        question_sets[category_id] = {
            "category_name": category_name,
            "category_description": category_description,
            "current_version": 1,
            "versions": [{**version_record, "version": 1}],
        }
        return category_id

    return _state_store.update_state(_question_sets_file(), _mutate)


def add_question_set_version(category_id: str, version_record: dict) -> int:
    """
    Append `version_record` as the next version of `category_id` and make
    it current. Older versions are kept. Returns the new version number.
    Raises KeyError if the category doesn't exist.
    """

    def _mutate(question_sets: dict) -> int:
        record = question_sets[category_id]
        new_version = max((v.get("version", 0) for v in record.get("versions", [])), default=0) + 1
        record.setdefault("versions", []).append({**version_record, "version": new_version})
        record["current_version"] = new_version
        return new_version

    return _state_store.update_state(_question_sets_file(), _mutate)
