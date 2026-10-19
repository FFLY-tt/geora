"""
The block "Is your website ready for AI?" of the result page: one score and one level per
check of the company's own website, a total, and the sentence under them. Computed from the
dimension scores already in the result; no new request and no model call.

The checks are topic_relevance, content_recency, evidence_density and technical_readability.
fact_consistency (do review sites agree) is not part of it; it stays in the details.
"""

from decimal import ROUND_HALF_UP, Decimal

import engines.citation_engine as citation_engine

# The four checks, in the order the page shows them, with the name the page shows.
CHECKS = [
    ("topic_relevance", "Answers customer questions"),
    ("evidence_density", "Has real numbers and proof"),
    ("content_recency", "Up to date"),
    ("technical_readability", "Easy for AI to read"),
]

# Where a score becomes "Good" and "Needs work". These lines are our own judgment, not a standard.
GOOD_FROM = 70
NEEDS_WORK_FROM = 40

LEVEL_GOOD = "Good"
LEVEL_NEEDS_WORK = "Needs work"
LEVEL_WEAK = "Weak"


def level_for(score: int | None) -> str | None:
    """The level word for a score, or None when there is no score."""
    if score is None:
        return None
    if score >= GOOD_FROM:
        return LEVEL_GOOD
    if score >= NEEDS_WORK_FROM:
        return LEVEL_NEEDS_WORK
    return LEVEL_WEAK


def _round_half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def answers_using_own_site(citation_details: dict, domain: str) -> tuple[int, int]:
    """(answers whose sources include the company's own website, answers we got)."""
    domain = (domain or "").lower()
    used = total = 0
    for entry in citation_details.get("per_query_results") or []:
        for run in entry.get("runs") or []:
            if run.get("status") != "ok":
                continue
            total += 1
            if any(citation_engine._domain_matches_citation(c, domain) for c in run.get("all_citations") or []):
                used += 1
    return used, total


def build_site_readiness(dimensions: list[dict], domain: str) -> dict:
    """
    {total, level, items, sentence, fix_first}. The total is the plain average of the checks that
    have a result, rounded half up; using the average is our own judgment. A check without a
    result (score -1) is left out of the average and has no score or level. When no check has a
    result there is no total at all.
    """
    by_name = {dim["dimension"]: dim for dim in dimensions}
    items = []
    for key, name in CHECKS:
        score = (by_name.get(key) or {}).get("score")
        score = score if isinstance(score, int) and score >= 0 else None
        items.append({"key": key, "name": name, "score": score, "level": level_for(score)})

    scored = [item for item in items if item["score"] is not None]
    if not scored:
        return {"total": None, "level": None, "items": items, "sentence": "We could not read your website.", "fix_first": []}

    total = _round_half_up(sum(item["score"] for item in scored) / len(scored))
    if total >= GOOD_FROM:
        used, answers = answers_using_own_site((by_name.get("citation_visibility") or {}).get("details") or {}, domain)
        if used == 0:
            sentence = "Your website is in good shape. AI just isn't reading it for these questions yet."
        else:
            sentence = f"Your website is in good shape. AI used it in {used} of {answers} answers."
        return {"total": total, "level": level_for(total), "items": items, "sentence": sentence, "fix_first": []}

    # The lowest check always, and a second one only when it is also below the good line.
    order = {item["key"]: index for index, item in enumerate(items)}
    lowest = sorted(scored, key=lambda item: (item["score"], order[item["key"]]))
    fix_first = [lowest[0]["name"]] + [item["name"] for item in lowest[1:2] if item["score"] < GOOD_FROM]
    return {"total": total, "level": level_for(total), "items": items, "sentence": "Start by fixing these:", "fix_first": fix_first}
