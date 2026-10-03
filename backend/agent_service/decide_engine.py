"""
Decide: a deterministic rule engine that turns a diagnosis result (the
`dimensions` list returned by diagnose_service's POST /api/diagnose)
into a ranked "fix these first" list.

No LLM calls, no external APIs, no agent framework. Every tunable
number lives in REASON_DIMENSIONS below, so the ranking can be retuned
without touching the logic that reads it. Given the same input, this
always produces the same output.

citation_visibility is treated as an outcome metric, not something to
fix directly, so it is reported separately under `outcome` and never
enters the ranking.
"""

OUTCOME_DIMENSION = "citation_visibility"

PRIORITY_FORMULA = "priority = ((100 - score) * weight) / fix_cost"

# The 4 reason dimensions that drive the ranking. `weight_basis` records
# where each weight came from, so a reader can tell a measured number
# from a judgment call. `action` is a fixed, plain-language template for
# this dimension; it never promises a specific amount of improvement.
REASON_DIMENSIONS: dict[str, dict] = {
    "content_recency": {
        "weight": 0.68,
        "weight_basis": "measured: GEO-16 study, B2B SaaS, freshness r=0.68",
        "fix_cost": 1,
        "deliverable": "technical_package",
        "deliverable_status": "available",
        "action": "Refresh the content on your most visited pages, then show the real date it was updated. Changing the date without changing the content does not count.",
    },
    "fact_consistency": {
        "weight": 0.5,
        "weight_basis": "judgment",
        "fix_cost": 1,
        "deliverable": "review_profile_draft",
        "deliverable_status": "planned",
        "action": "Make sure the facts about your company match everywhere customers might see them, including review sites.",
    },
    "evidence_density": {
        "weight": 0.6,
        "weight_basis": "research-supported: KDD 2024 GEO paper",
        "fix_cost": 2,
        "deliverable": "website_copy",
        "deliverable_status": "planned",
        "action": "Replace vague claims on your site with concrete numbers and specifics that back them up.",
    },
    "topic_relevance": {
        "weight": 0.4,
        "weight_basis": "judgment",
        "fix_cost": 2,
        "deliverable": "answer_capsules",
        "deliverable_status": "available",
        "action": "Give customers direct, self-contained answers to the real questions they are asking in this category.",
    },
}


def _sort_key(item: dict) -> tuple:
    """Highest priority first, then highest impact, then dimension name ascending, for a fully stable order."""
    return (-item["priority"], -item["impact"], item["dimension"])


def _build_action_item(dimension: str, score: int, config: dict) -> dict:
    gap = 100 - score
    impact = round(gap * config["weight"], 2)
    priority = round(impact / config["fix_cost"], 1)
    return {
        "dimension": dimension,
        "score": score,
        "gap": gap,
        "weight": config["weight"],
        "weight_basis": config["weight_basis"],
        "fix_cost": config["fix_cost"],
        "impact": impact,
        "priority": priority,
        "action": config["action"],
        "deliverable": config["deliverable"],
        "deliverable_status": config["deliverable_status"],
    }


def decide(domain: str, company_name: str, dimensions: list[dict]) -> dict:
    """
    Build the ranked fix-this-first list for one diagnosis result.

    `dimensions` is a list of dicts shaped like
    {"dimension": str, "score": int, "details": dict}. Unrecognized
    dimension names are ignored and reported in `warnings`. A score of
    -1 means no real data was available for that dimension; it is
    reported in `needs_data` and never ranked or guessed at.
    """
    outcome = None
    ranked_candidates = []
    needs_data = []
    warnings = []

    for entry in dimensions:
        dimension = entry["dimension"]
        score = entry["score"]
        details = entry.get("details") or {}

        if dimension == OUTCOME_DIMENSION:
            outcome = {"dimension": dimension, "score": score}
            continue

        config = REASON_DIMENSIONS.get(dimension)
        if config is None:
            warnings.append(f"Unrecognized dimension '{dimension}' was ignored.")
            continue

        if score == -1:
            needs_data.append({"dimension": dimension, "reason": details.get("reason", "No reason provided.")})
            continue

        ranked_candidates.append(_build_action_item(dimension, score, config))

    ranked_candidates.sort(key=_sort_key)
    all_ranked = [{"rank": index + 1, **item} for index, item in enumerate(ranked_candidates)]
    # A priority of 0 means the score is already perfect for that
    # dimension, so there is nothing to fix this week even though it
    # still belongs in all_ranked for a complete picture.
    this_week = [item for item in all_ranked if item["priority"] > 0][:3]

    method = {
        "formula": PRIORITY_FORMULA,
        "weights": {
            name: {"weight": config["weight"], "weight_basis": config["weight_basis"]}
            for name, config in REASON_DIMENSIONS.items()
        },
    }

    return {
        "domain": domain,
        "company_name": company_name,
        "outcome": outcome,
        "this_week": this_week,
        "all_ranked": all_ranked,
        "needs_data": needs_data,
        "warnings": warnings,
        "method": method,
    }
