"""
Evidence density engine.

Fetches the target domain's homepage, extracts the visible body text,
and asks a DeepSeek chat model to find concrete evidence on the page —
specific numbers/statistics, named customers or case studies, quotes
attributed to a named person, and third-party certifications or
sources — each backed by an exact quote from the page.

Page fetching itself is delegated to shared._fetch_utils.fetch_page_text
(shared with fact_engine.py etc.) — see that module's docstring for the
note about anti-bot limitations and the Jina AI Reader API as a future
upgrade.

Calls fetch_page_text with method="full" (whole-page BeautifulSoup
extraction) rather than the "main" default (trafilatura-first).
Investigation found trafilatura treats homepage testimonial quotes and
stat cards ("$100,000 saved", "50% increase", named customers) as
boilerplate and strips them — on close.com this measured a ~16 point
drop in evidence_density's score, confirmed across 3 repeated calls on
each side, not AI scoring noise. For this engine specifically, that
dropped content is not noise to clean up, it IS the evidence being
judged, so "full" is the correct mode here even though every other
caller of fetch_page_text keeps the "main" default.

Scoring is now split in two, after backend/scripts/stability_audit.py
showed that a single holistic "give this page a 0-100 score" LLM call
is itself unstable even on byte-identical input (observed 42-62 on
identical close.com text, confirmed again on feroot.com and close.com
by 3 independent, concurrent calls on the same page text producing 15
different raw outputs). The LLM's only job now is to find and quote
evidence items; every quote is verified as a real, word-for-word
substring of the actual page text (an unverifiable quote is dropped,
never trusted), and the SCORE is computed by this code from the
verified item counts via a fixed, documented rule (see
_points_for_item_count) — not decided by the model. That listing call
still runs JUDGMENT_RUNS times concurrently and the results are
unioned together (deduplicated by category+quote), since more
independent passes over the same page tend to catch more of the real
evidence actually on it.

The listing+scoring result for a given page's exact text is cached by
engines._judgment_cache: re-diagnosing a domain whose homepage text
hasn't changed at all returns the identical score every time, with no
LLM call, instead of re-rolling the model's own non-determinism. See
that module's docstring for why, and PROMPT_VERSION below for how a
prompt change invalidates old cache entries.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from shared.config import settings
from shared._fetch_utils import fetch_page_text
from engines._judgment_cache import get_cached_judgment, make_judgment_cache_key, save_judgment
from engines._plain_finding_generator import make_plain_finding
from models import DimensionScore

DIMENSION_NAME = "evidence_density"

# Bump this whenever _EVIDENCE_LISTING_SYSTEM_PROMPT's wording changes
# in a way that could make a previously-cached listing unrepresentative
# of the new prompt. Folded into the judgment cache key.
PROMPT_VERSION = "v1-evidence-listing"

# The page is listed for evidence this many times, concurrently, and
# the verified items from every successful run are unioned together
# (see _aggregate_items) before the code computes a score from them.
JUDGMENT_RUNS = 3

# The 4 kinds of concrete evidence this engine looks for. Fixed, not
# something the LLM invents per call, so counts are always comparable
# across pages and runs.
EVIDENCE_CATEGORIES = [
    "number_or_statistic",
    "named_customer_or_case_study",
    "attributed_quote",
    "third_party_source_or_certification",
]

_CATEGORY_LABELS = {
    "number_or_statistic": "specific numbers or statistics",
    "named_customer_or_case_study": "named customers or case studies",
    "attributed_quote": "quotes attributed to a named person",
    "third_party_source_or_certification": "third-party certifications or sources",
}

# Our own scoring rule, applied by this code, not the LLM: each of the
# 4 categories is worth up to MAX_POINTS_PER_CATEGORY points, based on
# how many distinct, page-verified items of that category were found.
# 3+ items earns full marks for that category; fewer earns a smaller,
# fixed amount — rewarding some real evidence over none, while still
# requiring genuine density (not just one token statistic) for full
# marks. 4 categories x 25 points = 100 max, matching every other
# dimension's 0-100 scale.
MAX_POINTS_PER_CATEGORY = 25
_POINTS_BY_ITEM_COUNT = {0: 0, 1: 15, 2: 20}


def _points_for_item_count(count: int) -> int:
    """Our own fixed rule mapping a category's verified item count to points. See MAX_POINTS_PER_CATEGORY."""
    if count >= 3:
        return MAX_POINTS_PER_CATEGORY
    return _POINTS_BY_ITEM_COUNT.get(count, 0)


_EVIDENCE_LISTING_SYSTEM_PROMPT = """
ROLE
You are a strict content auditor working on behalf of an outside analyst. You will be given the visible text of a THIRD PARTY company's homepage. That company is not you, and this task is not about you: you are reading their page on their behalf, the same way an auditor reviews a client's document. Never describe yourself, your own identity, what kind of system you are, or how you work. Only describe what is actually written on the page you were given.

TASK
Find every piece of concrete evidence on the page that backs up a claim, sorted into exactly these 4 categories:
- "number_or_statistic": a specific number, percentage, or statistic (e.g. "50% increase", "11,500+ customers", "$100,000 saved").
- "named_customer_or_case_study": a specific named customer, company, or case study mentioned by name.
- "attributed_quote": a quote attributed to a specific named person (a title or company alone does not count; an anonymous "customers say..." does not count).
- "third_party_source_or_certification": a named third-party certification, award, rating, or source (e.g. "G2 rating of 4.7", "SOC 2 certified", "as featured in TechCrunch").

For each item you find, quote the EXACT sentence or phrase from the page that contains it, copied word for word exactly as it appears, with no changes. Never invent, paraphrase, or combine separate parts of the page into one quote. If a category genuinely has no evidence on the page, leave it with no items at all — never force something into a category it does not really belong in.

Respond with ONLY a JSON object, no other text, no markdown formatting, no code fences, in this exact shape:
{"items": [{"category": "<one of the 4 category names above>", "quote": "<exact quote copied from the page>"}]}
""".strip()


def _call_deepseek_for_evidence_listing(page_text: str) -> list:
    """
    Send the page text to DeepSeek and return the raw list of claimed
    evidence items (not yet verified against the real page text — see
    _verify_and_filter_items). Raises requests.RequestException on
    network/HTTP failure, ValueError if the response isn't valid JSON
    in the expected shape.
    """
    url = f"{settings.deepseek_api_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    # Truncate to keep the request reasonably sized; homepages rarely need
    # more than this to find the real evidence present on them.
    truncated_text = page_text[:8000]
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": _EVIDENCE_LISTING_SYSTEM_PROMPT},
            {"role": "user", "content": truncated_text},
        ],
        "temperature": 0,
    }
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=settings.request_timeout_seconds,
    )
    response.raise_for_status()
    raw_response = response.json()

    content = raw_response["choices"][0]["message"]["content"]
    # Models occasionally wrap JSON in a code fence despite instructions;
    # strip that defensively before parsing.
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    parsed = json.loads(cleaned)

    if "items" not in parsed or not isinstance(parsed["items"], list):
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")

    return parsed["items"]


def _verify_and_filter_items(items: list, page_text: str) -> tuple[list, list]:
    """
    Keep only claimed items whose quote is a real, word-for-word
    substring of `page_text` and whose category is one of
    EVIDENCE_CATEGORIES. Never trust the model's own claim that a
    quote is real without checking it against the actual fetched
    text — this is what stops a fabricated or paraphrased "quote"
    from ever reaching the score.

    Returns (verified_items, dropped_items): dropped_items records
    what was rejected and why, kept for debugging/transparency.
    """
    verified = []
    dropped = []
    for raw_item in items:
        if not isinstance(raw_item, dict):
            dropped.append({"item": raw_item, "reason": "not an object"})
            continue
        category = raw_item.get("category")
        quote = (raw_item.get("quote") or "").strip()
        if category not in EVIDENCE_CATEGORIES:
            dropped.append({"item": raw_item, "reason": f"unrecognized category: {category!r}"})
            continue
        if not quote or quote not in page_text:
            dropped.append({"item": raw_item, "reason": "quote not found verbatim in page text"})
            continue
        verified.append({"category": category, "quote": quote})
    return verified, dropped


def _run_single_listing(page_text: str, run_index: int) -> dict:
    """
    Run one full evidence-listing call and verify its claimed items.
    Never raises: a failure is recorded as an "error" entry and simply
    excluded from the aggregated union, the same way a failed run is
    excluded in this project's other multi-run engines.
    """
    try:
        raw_items = _call_deepseek_for_evidence_listing(page_text)
    except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {"run": run_index, "status": "error", "error": str(exc)}

    verified_items, dropped_items = _verify_and_filter_items(raw_items, page_text)
    return {
        "run": run_index,
        "status": "ok",
        "verified_items": verified_items,
        "dropped_items": dropped_items,
    }


def _run_listings_concurrently(page_text: str) -> list:
    """
    Run JUDGMENT_RUNS independent evidence-listing calls concurrently
    (each is its own DeepSeek call with no shared state, so there's
    nothing to race on) and return the results in run-index order,
    regardless of which call actually finished first.
    """
    results: list = [None] * JUDGMENT_RUNS  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=JUDGMENT_RUNS) as executor:
        future_to_run = {
            executor.submit(_run_single_listing, page_text, run_index): run_index
            for run_index in range(1, JUDGMENT_RUNS + 1)
        }
        for future in as_completed(future_to_run):
            run_index = future_to_run[future]
            results[run_index - 1] = future.result()
    return results


def _aggregate_items(runs: list) -> list:
    """
    Union the verified items from every successful run, deduplicated
    by (category, quote) — the same real evidence item found again by
    a second or third pass counts once, not multiple times, since that
    would artificially inflate the score rather than reflect anything
    new found on the page.
    """
    seen = set()
    aggregated = []
    for run in runs:
        if run["status"] != "ok":
            continue
        for item in run["verified_items"]:
            key = (item["category"], item["quote"])
            if key in seen:
                continue
            seen.add(key)
            aggregated.append(item)
    return aggregated


def _score_from_items(items: list) -> tuple[int, dict, dict]:
    """
    Compute the 0-100 score from verified items via _points_for_item_count
    — OUR rule, applied to the model's (verified) findings, not a score
    the model itself decided. Returns (score, counts_by_category,
    points_by_category).
    """
    counts = {category: 0 for category in EVIDENCE_CATEGORIES}
    for item in items:
        counts[item["category"]] += 1
    points_by_category = {category: _points_for_item_count(count) for category, count in counts.items()}
    score = sum(points_by_category.values())
    return score, counts, points_by_category


def _build_evidence_summary_text(items: list, counts: dict) -> str:
    """
    Deterministic, code-generated description of what evidence was
    found and in what quantity — not the model's own free-text
    reasoning (there is none anymore; see module docstring).
    """
    lines = []
    for category in EVIDENCE_CATEGORIES:
        count = counts[category]
        label = _CATEGORY_LABELS[category]
        if count == 0:
            lines.append(f"No {label} were found on the page.")
            continue
        examples = [item["quote"] for item in items if item["category"] == category][:2]
        examples_text = "; ".join(f'"{example}"' for example in examples)
        lines.append(f"{count} {label} found, for example: {examples_text}")
    return " ".join(lines)


def check_evidence_density(domain: str) -> DimensionScore:
    """
    Score how evidence-dense (vs. vague marketing) the domain's homepage
    copy is: concrete evidence items are found and verified by DeepSeek,
    the score is computed by this code from the verified counts (see
    _score_from_items), and the result is cached per exact page text
    (engines._judgment_cache) so a repeated diagnosis against unchanged
    content returns the same score with no LLM call.

    Returns score == -1 ("insufficient data") when the homepage could not
    be fetched, or when DeepSeek is not configured / fails to produce a
    valid listing. Never fabricates a score in those cases.
    """
    try:
        page_text = fetch_page_text(domain, method="full")
    except requests.RequestException as exc:
        fallback = (
            f"We couldn't open the homepage at {domain} to see what's actually written on it, so "
            "there's nothing real to judge here. This usually means the site blocked our visit or "
            "was down when we checked. Today, try loading the homepage yourself in an incognito "
            "tab to confirm it's reachable."
        )
        facts_summary = (
            "Dimension: whether the company's homepage backs up its claims with real, specific "
            "evidence instead of vague marketing language.\n"
            f"Status: We could not open the homepage at {domain} to read it. The site may be blocking "
            "automated visits or was temporarily down."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": f"Failed to fetch homepage for '{domain}': {exc}",
                "suggestion": "Consider retrying via the Jina AI Reader API for sites that block plain HTTP clients.",
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    if not page_text:
        fallback = (
            f"We could open the homepage at {domain}, but found no real readable text on it to "
            "judge. Today, check that your homepage's actual selling copy shows up right away for "
            "a visitor, not just images or a loading animation."
        )
        facts_summary = (
            "Dimension: whether the company's homepage backs up its claims with real, specific "
            "evidence instead of vague marketing language.\n"
            f"Status: We opened the homepage at {domain} but found no readable text on it."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": f"Homepage for '{domain}' returned no extractable body text (possibly JS-rendered).",
                "suggestion": "Consider retrying via the Jina AI Reader API, which renders JavaScript server-side.",
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    if not settings.has_deepseek_key():
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": "DEEPSEEK_API_KEY is not configured; homepage text was fetched but not judged.",
                "fetched_text_preview": page_text[:500],
                "plain_finding": (
                    "We fetched the homepage successfully, but the tool that reads it and judges whether "
                    "the content is backed by real substance isn't turned on yet, so we don't have a real "
                    "verdict. Today, turn on that connection so this check can run for real next time."
                ),
            },
        )

    cache_key = make_judgment_cache_key(DIMENSION_NAME, PROMPT_VERSION, settings.deepseek_model, page_text)
    cached = get_cached_judgment(cache_key)

    if cached is not None:
        score = cached["score"]
        items = cached["items"]
        counts = cached["counts"]
        points_by_category = cached["points_by_category"]
        runs = cached["runs"]
        cache_status = "cache_hit"
    else:
        runs = _run_listings_concurrently(page_text)
        successful_runs = [run for run in runs if run["status"] == "ok"]

        if not successful_runs:
            errors = "; ".join(f"run {run['run']}: {run['error']}" for run in runs)
            fallback = (
                "We fetched the homepage, but hit a technical hiccup while looking for real evidence "
                "on it, so there's no real verdict this run. This is usually temporary. Today, just "
                "try running the report again."
            )
            facts_summary = (
                "Dimension: whether the company's homepage backs up its claims with real, specific "
                "evidence instead of vague marketing language.\n"
                "Status: A temporary technical problem stopped this check from finishing."
            )
            plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
            return DimensionScore(
                dimension=DIMENSION_NAME,
                score=-1,
                details={
                    "status": "insufficient_data",
                    "reason": f"DeepSeek evidence listing failed on all {JUDGMENT_RUNS} runs: {errors}",
                    "runs": runs,
                    "fetched_text_preview": page_text[:500],
                    "generation_log": generation_log,
                    "plain_finding": plain_finding,
                },
            )

        items = _aggregate_items(runs)
        score, counts, points_by_category = _score_from_items(items)
        cache_status = "fresh_judgment"
        save_judgment(
            cache_key,
            {"score": score, "items": items, "counts": counts, "points_by_category": points_by_category, "runs": runs},
        )

    evidence_summary = _build_evidence_summary_text(items, counts)
    fallback = _build_fallback_plain_finding(score, evidence_summary)
    facts_summary = _build_facts_summary(score, evidence_summary)
    plain_finding, generation_log = make_plain_finding(facts_summary, fallback)

    return DimensionScore(
        dimension=DIMENSION_NAME,
        score=score,
        details={
            "status": "ok",
            "items": items,
            "counts_by_category": counts,
            "points_by_category": points_by_category,
            "scoring_rule": (
                "Our own fixed rule, not an LLM judgment: each of the 4 evidence categories is worth up "
                f"to {MAX_POINTS_PER_CATEGORY} points based on verified item count "
                f"({_POINTS_BY_ITEM_COUNT}, {MAX_POINTS_PER_CATEGORY} points for 3 or more)."
            ),
            "cache_status": cache_status,
            "runs": runs,
            "fetched_text_preview": page_text[:500],
            "fetched_text_length": len(page_text),
            "generation_log": generation_log,
            "plain_finding": plain_finding,
        },
    )


def _build_facts_summary(score: int, evidence_summary: str) -> str:
    """Plain-text real facts handed to the AI writer — see engines._plain_finding_generator."""
    return (
        "Dimension: whether the company's homepage backs up its claims with real, specific evidence, "
        "such as numbers, named examples, or sources, instead of vague marketing language.\n"
        f"An analysis of the homepage content found: {evidence_summary}"
    )


def _build_fallback_plain_finding(score: int, evidence_summary: str) -> str:
    """Founder-readable finding built on top of the real evidence summary for this run, used if AI writing fails."""
    if score >= 70:
        return (
            f"{evidence_summary} That's a good sign. Content backed by specifics like this is exactly "
            "what AI search tools and skeptical customers tend to trust and quote. Today, take that "
            "same specific, detail-backed style and apply it to one more page that's currently thinner "
            "on substance."
        )
    if score <= 30:
        return (
            f"{evidence_summary} In practice, that means the homepage leans on broad claims a customer "
            "can't verify, rather than specifics they can check for themselves. Both AI search tools and "
            "cautious customers tend to discount that kind of copy. Today, pick the single biggest claim "
            "on the homepage and add one concrete number, named example, or source next to it."
        )
    return (
        f"{evidence_summary} That's a mixed picture. Part of the page backs itself up with specifics, "
        "and part leans on broader claims that are harder for a customer, or an AI search tool, to "
        "verify. Today, find one broad claim on the homepage and swap it for a specific number or "
        "named example."
    )
