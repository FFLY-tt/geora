"""
Citation visibility engine.

Checks whether a company is actually cited by Perplexity (an AI answer
engine) when a real buyer asks about the problem space it operates in —
*without* already knowing the company's name.

Design note: an earlier version of this engine built queries like "best
alternatives to {company_name}" directly from the company name. That is a
logical error: putting the brand name in the query all but guarantees the
AI answer will mention that brand, so the resulting score measured
nothing real. The fix is to never let the brand name reach the query:

1. Classify the company's buyer-facing category from its own homepage
   text (via DeepSeek), described the way an analyst/buyer would put it,
   not the company's own marketing language.
2. Generate brand-free buyer questions for that category (also via
   DeepSeek) — questions a buyer would ask before knowing any vendor name.
3. Query Perplexity with only those brand-free questions, and check
   whether the company or its domain shows up organically in the answer
   or citations.

Any generated batch of questions that turns out to contain the company
name or domain is discarded and regenerated — it never reaches Perplexity
or the score.

Every returned DimensionScore also carries a `plain_finding` string: a
founder-readable explanation (what's wrong, why it happens in business
terms, and one concrete thing to do today) built only from this run's
real data. All the technical fields below it (category, hit_queries,
etc.) are kept as-is for debugging.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from shared.config import settings
from shared._fetch_utils import fetch_page_text
from engines._plain_finding_generator import make_plain_finding
from engines._query_cache import CACHE_TTL, get_cached_queries, save_queries
from engines._source_map import build_source_map
from shared._text_format import PLAIN_LANGUAGE_FORMAT_RULES, find_banned_formatting, sanitize_banned_formatting
from models import DimensionScore

DIMENSION_NAME = "citation_visibility"

MAX_GENERATION_ATTEMPTS = 3

# Each of the 5 brand-free queries is sent to Perplexity this many times,
# since a single real-world call can hit or miss on its own regardless of
# genuine visibility; averaging across repeats turns that into an actual
# mention RATE per question instead of a one-shot coin flip.
RUNS_PER_QUERY = 3

# Upper bound on how many of the RUNS_PER_QUERY * REQUIRED_QUERY_COUNT
# Perplexity calls are in flight at once.
MAX_CONCURRENT_CALLS = 5

_CATEGORY_AND_QUERY_SYSTEM_PROMPT = f"""
You are a B2B market research analyst. You will be given the visible text
of a company's homepage. Do two things:

1. Identify the specific, narrow customer-facing category or niche this
company competes in. Describe it the way an independent industry analyst
or a customer researching solutions would describe it, NOT using the
company's own marketing language, and NOT using the company's name.

2. Generate exactly 5 realistic questions or search queries that a
customer in this category would type into an AI assistant BEFORE knowing
about any specific vendor. Each question must describe a problem, task,
or need in the category. They must NOT mention this company's name, its
domain, or any of its specific product names.

Respond with ONLY a JSON object, no other text, in this exact shape:
{{"category": "<short customer-facing category description>", "queries": ["<query 1>", "<query 2>", "<query 3>", "<query 4>", "<query 5>"]}}

{PLAIN_LANGUAGE_FORMAT_RULES}
""".strip()

# Fixed at exactly 5 (not "3 to 5") so the "N out of M queries hit" ratio
# means the same thing on every run — a variable count would make scores
# from different runs incomparable even if visibility genuinely didn't change.
REQUIRED_QUERY_COUNT = 5


def _call_deepseek_for_category_and_queries(homepage_text: str) -> dict:
    """
    Ask DeepSeek to classify the company's buyer-facing category and
    generate brand-free buyer queries for it, based on real homepage text.
    Raises requests.RequestException on network/HTTP failure, and
    ValueError if the response isn't valid JSON in the expected shape.
    """
    url = f"{settings.deepseek_api_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    # Temperature 0 (not just "low") for this call specifically: the real
    # fix for run-to-run score instability is caching this result per
    # domain (see engines._query_cache) so the exact same questions get
    # reused, not regenerated. But whenever we DO have to regenerate (cache
    # miss/expiry), temperature 0 minimizes wording drift between that
    # fresh generation and whatever was cached before.
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": _CATEGORY_AND_QUERY_SYSTEM_PROMPT},
            {"role": "user", "content": homepage_text[:8000]},
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

    if "category" not in parsed or "queries" not in parsed:
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")

    return parsed


def _generate_category_and_brand_free_queries(
    company_name: str, domain: str, homepage_text: str
) -> tuple[str | None, list[str] | None, list[dict]]:
    """
    Repeatedly ask DeepSeek for a (category, queries) pair until it
    produces exactly REQUIRED_QUERY_COUNT queries that do not contain the
    company name or domain anywhere. A batch that is contaminated OR is
    not exactly the required count is discarded entirely — never trimmed,
    padded, or reused — and counts as a failed attempt. Requiring an exact
    count (rather than accepting "however many came back") keeps the
    "N out of 5 queries hit" ratio comparable across separate runs.

    A batch is also checked for banned formatting (em dashes, double
    hyphens, parentheses, brackets) in the category and every query. That
    alone does not disqualify an otherwise valid batch the way brand
    contamination or a wrong count does: on the final attempt, a batch
    that is unbiased and correctly sized but still has formatting issues
    is sanitized and used, instead of discarding good test data over a
    stray character.

    Returns (category, queries, attempts_log). category/queries are None
    if no valid, correctly-sized batch was produced within
    MAX_GENERATION_ATTEMPTS.
    Propagates requests.RequestException / ValueError / json.JSONDecodeError
    from the underlying API call immediately (a network/API failure is not
    retried as if it were a contamination problem).
    """
    company_name_lower = company_name.lower()
    domain_lower = domain.lower()
    attempts_log = []

    for attempt_number in range(1, MAX_GENERATION_ATTEMPTS + 1):
        result = _call_deepseek_for_category_and_queries(homepage_text)
        category = (result.get("category") or "").strip()
        raw_queries = [q.strip() for q in result.get("queries", []) if isinstance(q, str) and q.strip()]

        contains_brand = any(
            company_name_lower in q.lower() or (domain_lower and domain_lower in q.lower()) for q in raw_queries
        )
        wrong_count = len(raw_queries) != REQUIRED_QUERY_COUNT
        is_last_attempt = attempt_number == MAX_GENERATION_ATTEMPTS

        if category and raw_queries and not contains_brand and not wrong_count:
            format_issues = set(find_banned_formatting(category))
            for query in raw_queries:
                format_issues.update(find_banned_formatting(query))

            if not format_issues:
                attempts_log.append(
                    {
                        "attempt": attempt_number,
                        "outcome": "accepted",
                        "category": category,
                        "queries": raw_queries,
                    }
                )
                return category, raw_queries, attempts_log

            if is_last_attempt:
                # Substantively valid (unbiased, right count) but still
                # has formatting issues after every retry: sanitize
                # deterministically rather than discarding good test data
                # over a stray em dash or parenthesis.
                category = sanitize_banned_formatting(category)
                raw_queries = [sanitize_banned_formatting(query) for query in raw_queries]
                attempts_log.append(
                    {
                        "attempt": attempt_number,
                        "outcome": "accepted_sanitized",
                        "issues": sorted(format_issues),
                        "category": category,
                        "queries": raw_queries,
                    }
                )
                return category, raw_queries, attempts_log

            attempts_log.append(
                {
                    "attempt": attempt_number,
                    "outcome": "rejected",
                    "reason": f"Banned formatting found: {', '.join(sorted(format_issues))}",
                    "category": category,
                    "rejected_queries": raw_queries,
                }
            )
            continue

        if contains_brand:
            reason = "Generated queries contained the brand name or domain"
        elif wrong_count:
            reason = f"Expected exactly {REQUIRED_QUERY_COUNT} queries, got {len(raw_queries)}"
        else:
            reason = "DeepSeek returned an empty category or query list"

        attempts_log.append(
            {
                "attempt": attempt_number,
                "outcome": "rejected",
                "reason": reason,
                "category": category or None,
                "rejected_queries": raw_queries,
            }
        )

    return None, None, attempts_log


def _call_perplexity(query: str) -> dict:
    """
    Send one query to the Perplexity chat completions API and return the
    raw JSON response. Raises requests.RequestException on network/HTTP
    failure so the caller can distinguish "no data" from "empty result".
    """
    url = f"{settings.perplexity_api_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.perplexity_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    payload = {
        "model": settings.perplexity_model,
        "messages": [
            {"role": "user", "content": query},
        ],
    }
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=settings.request_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def _execute_single_run(query: str, domain_lower: str, company_lower: str) -> dict:
    """
    One Perplexity call for `query`. Whether a mention "counts" is decided
    by plain, deterministic lowercase substring matching, same as before:
    domain found in a citation URL, or the company name found in the
    answer text. Never raises: a request failure is reported as status
    "error" so RUNS_PER_QUERY of these can run per query without one
    failed call aborting the others.
    """
    try:
        raw_response = _call_perplexity(query)
    except requests.RequestException as exc:
        return {"status": "error", "error": str(exc)}

    choices = raw_response.get("choices", [])
    answer_text = ""
    if choices:
        answer_text = choices[0].get("message", {}).get("content", "") or ""

    # Perplexity's "sonar" models return a top-level "citations" field: a
    # list of source URLs used to ground the answer.
    citations = raw_response.get("citations", []) or []

    matched_citations = [c for c in citations if domain_lower in c.lower()]
    company_mentioned_in_answer = company_lower in answer_text.lower()
    is_hit = bool(matched_citations) or company_mentioned_in_answer

    return {
        "status": "ok",
        "answer_text": answer_text,
        "all_citations": citations,
        "matched_citations": matched_citations,
        "company_mentioned_in_answer": company_mentioned_in_answer,
        "is_hit": is_hit,
    }


def _run_queries_multiple_times(queries: list[str], domain_lower: str, company_lower: str) -> dict[str, list[dict]]:
    """
    Call Perplexity RUNS_PER_QUERY times for each query (REQUIRED_QUERY_COUNT
    * RUNS_PER_QUERY calls total, e.g. 5 * 3 = 15), with at most
    MAX_CONCURRENT_CALLS calls in flight at once via a plain thread pool —
    _call_perplexity is a blocking requests call, so threads, not asyncio,
    are what let these run concurrently.

    Returns {query: [run_1_result, run_2_result, run_3_result]}. Each
    result is placed into its run's own slot by run index rather than by
    completion order, so which Perplexity call happens to finish first
    never changes the run numbering in the result.
    """
    runs_by_query: dict[str, list[dict | None]] = {query: [None] * RUNS_PER_QUERY for query in queries}

    def _work(query: str, run_index: int) -> tuple[str, int, dict]:
        result = _execute_single_run(query, domain_lower, company_lower)
        result["run"] = run_index + 1
        return query, run_index, result

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CALLS) as executor:
        futures = [
            executor.submit(_work, query, run_index)
            for query in queries
            for run_index in range(RUNS_PER_QUERY)
        ]
        for future in as_completed(futures):
            query, run_index, result = future.result()
            runs_by_query[query][run_index] = result

    return runs_by_query


def _build_plain_finding_success(
    category: str,
    valid_query_count: int,
    average_mention_rate: float,
    per_query_results: list,
) -> str:
    """Founder-readable finding for a completed run, built only from real counts and quotes."""
    valid_entries = sorted(
        (entry for entry in per_query_results if entry["mention_rate"] is not None),
        key=lambda entry: entry["mention_rate"],
    )
    worst = valid_entries[0]
    best = valid_entries[-1]
    average_pct = round(average_mention_rate * 100)
    total_calls = valid_query_count * RUNS_PER_QUERY

    if average_pct == 0:
        return (
            f"We asked AI search tools {valid_query_count} realistic questions a customer in the "
            f'"{category}" space would ask before knowing any vendor names, like "{worst["query"]}", and '
            f"asked each one {RUNS_PER_QUERY} separate times. Not one of those {total_calls} answers "
            "mentioned this brand or linked to its site. That means right now, if a customer starts their "
            "research on AI instead of a search engine, they consistently won't run into this brand at "
            "all. Whichever competitors do get mentioned win that customer's attention first. Today, "
            f'publish one page that directly and plainly answers a question like "{worst["query"]}", since '
            "AI tools tend to quote content that answers a question head on."
        )

    if average_pct == 100:
        return (
            f"We asked AI search tools {valid_query_count} realistic customer questions about the "
            f'"{category}" space, without ever mentioning this brand by name, and asked each one '
            f'{RUNS_PER_QUERY} separate times. It came up naturally in every single answer, for example '
            f'when asked "{best["query"]}". That is a strong sign customers researching this category '
            "through AI are already running into this brand consistently, not just by luck on one try. "
            "Today, keep an eye on this over time. AI answers shift as new content gets indexed, so what's "
            "working now may need refreshing in a few months."
        )

    return (
        f"We asked AI search tools {valid_query_count} realistic customer questions about the "
        f'"{category}" space, without mentioning this brand by name, and asked each one {RUNS_PER_QUERY} '
        f"separate times. Across all of those answers, this brand was mentioned an average of "
        f'{average_pct}% of the time, coming up most reliably for a question like "{best["query"]}" and '
        f'almost never for one like "{worst["query"]}". That is a mixed signal. This brand is on AI\'s '
        "radar for some customer questions but inconsistent or invisible for others, so some customers "
        "researching this category will simply never encounter it. Today, take the weakest question and "
        "write a page that answers it directly and specifically."
    )


def _build_facts_summary_success(
    category: str,
    valid_query_count: int,
    average_mention_rate: float,
    per_query_results: list,
) -> str:
    """Plain-text real facts handed to the AI writer — see engines._plain_finding_generator."""
    valid_entries = sorted(
        (entry for entry in per_query_results if entry["mention_rate"] is not None),
        key=lambda entry: entry["mention_rate"],
    )
    worst = valid_entries[0]
    best = valid_entries[-1]
    average_pct = round(average_mention_rate * 100)

    lines = [
        "Dimension: how often AI search tools mention this company when a customer researches its "
        "category without already knowing the brand name.",
        f'Category this company competes in: "{category}"',
        f"We tested {valid_query_count} realistic customer questions in this category, written so they "
        f"never mention the brand name, and asked each question {RUNS_PER_QUERY} separate times.",
        f"Across all of those answers, the brand was mentioned an average of {average_pct}% of the time.",
        f'Question mentioned most reliably: "{best["query"]}", mentioned in '
        f'{round(best["mention_rate"] * 100)}% of its answers.',
        f'Question mentioned least reliably: "{worst["query"]}", mentioned in '
        f'{round(worst["mention_rate"] * 100)}% of its answers.',
    ]
    return "\n".join(lines)


def check_citation_visibility(company_name: str, domain: str) -> DimensionScore:
    """
    Classify the company's buyer-facing category from its own homepage,
    generate brand-free buyer questions for that category, query
    Perplexity with them, and check whether the company or its domain
    shows up organically in the answer or citations.

    The (category, queries) pair is cached per domain (engines._query_cache)
    for CACHE_TTL: repeatedly regenerating fresh wording on every run was
    the actual root cause of citation_visibility swinging wildly between
    back-to-back runs against the same domain, since different wording
    produces different Perplexity searches. A cache hit skips both the
    homepage fetch and the DeepSeek call entirely.

    Returns a DimensionScore with score in [0, 100], or score == -1 when
    the check could not be performed at all (missing API keys, homepage
    unreachable, no brand-free queries could be generated, or every
    Perplexity request failing), meaning "insufficient data" rather than
    a real 0.
    """
    if not settings.has_perplexity_key():
        fallback = (
            "We couldn't test how AI search tools talk about this brand because the connection to "
            "those tools isn't turned on yet. There's no real answer to check for a mention. "
            "Today, turn on that connection so the next report can run a real test."
        )
        facts_summary = (
            "Dimension: how often AI search tools mention this company when a customer researches its "
            "category without already knowing the brand name.\n"
            "Status: We could not run this check because a required connection is not turned on."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": "PERPLEXITY_API_KEY is not configured; no query was performed.",
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    cached = get_cached_queries(domain)

    if cached:
        category, queries = cached
        generation_log = [
            {
                "outcome": "cache_hit",
                "category": category,
                "queries": queries,
                "note": f"Reused customer questions generated for this domain within the last {CACHE_TTL.days} days, instead of regenerating new wording.",
            }
        ]
    else:
        if not settings.has_deepseek_key():
            return DimensionScore(
                dimension=DIMENSION_NAME,
                score=-1,
                details={
                    "status": "insufficient_data",
                    "reason": "DEEPSEEK_API_KEY is not configured; cannot classify the company's category or generate customer queries.",
                    "plain_finding": (
                        "We couldn't run this check because the step that reads your homepage and works out "
                        "what customers actually search for isn't turned on yet. There was nothing fair to "
                        "test AI search results against. Today, turn on that connection so a real test can "
                        "run next time."
                    ),
                },
            )

        try:
            homepage_text = fetch_page_text(domain)
        except requests.RequestException:
            fallback = (
                f"We couldn't open the homepage at {domain} at all, so there was nothing real to check "
                "this brand's category against, and no fair test could be built. This usually means "
                "the site blocked our visit or was temporarily down when we checked. Today, try "
                "loading the homepage yourself in an incognito tab to confirm it's reachable."
            )
            facts_summary = (
                "Dimension: how often AI search tools mention this company when a customer researches "
                "its category without already knowing the brand name.\n"
                f"Status: We could not open the homepage at {domain} to figure out what category to "
                "test. It may be blocked or temporarily down."
            )
            plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
            return DimensionScore(
                dimension=DIMENSION_NAME,
                score=-1,
                details={
                    "status": "insufficient_data",
                    "reason": f"Failed to fetch homepage for '{domain}'",
                    "generation_log": generation_log,
                    "plain_finding": plain_finding,
                },
            )

        if not homepage_text:
            fallback = (
                f"We could open the homepage at {domain}, but found no real readable text on it. "
                "The visible copy your customers see may be loaded in a way our simple check can't "
                "read. Without real homepage text, we can't tell what category to test AI answers "
                "against. Today, check that your homepage's core copy shows up in the very first "
                "version of the page a visitor's browser receives, not just after extra loading."
            )
            facts_summary = (
                "Dimension: how often AI search tools mention this company when a customer researches "
                "its category without already knowing the brand name.\n"
                f"Status: We opened the homepage at {domain} but found no readable text on it."
            )
            plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
            return DimensionScore(
                dimension=DIMENSION_NAME,
                score=-1,
                details={
                    "status": "insufficient_data",
                    "reason": f"Homepage for '{domain}' returned no extractable body text (possibly JS-rendered).",
                    "generation_log": generation_log,
                    "plain_finding": plain_finding,
                },
            )

        try:
            category, queries, generation_log = _generate_category_and_brand_free_queries(
                company_name, domain, homepage_text
            )
        except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as exc:
            fallback = (
                "We hit a technical hiccup while working out what your customers search for before "
                "they know your brand, so there's no real test to report on this run. This is "
                "usually temporary. Today, just try running the report again."
            )
            facts_summary = (
                "Dimension: how often AI search tools mention this company when a customer researches "
                "its category without already knowing the brand name.\n"
                "Status: A temporary technical problem stopped this check from completing."
            )
            plain_finding, format_generation_log = make_plain_finding(facts_summary, fallback)
            return DimensionScore(
                dimension=DIMENSION_NAME,
                score=-1,
                details={
                    "status": "insufficient_data",
                    "reason": f"DeepSeek category/query generation failed: {exc}",
                    "generation_log": format_generation_log,
                    "plain_finding": plain_finding,
                },
            )

        if not queries:
            fallback = (
                "Every time we asked our tool to come up with customer questions about your "
                "category, it kept slipping your brand name or website into the questions "
                "themselves. That would make the test meaningless, like grading a quiz with the "
                "answer printed on the question. We refused to run the test on unfair questions, so "
                "there's no real score this time. Today, just try re-running the report. A fresh "
                "attempt from your homepage text often produces cleaner, brand-free questions."
            )
            facts_summary = (
                "Dimension: how often AI search tools mention this company when a customer researches "
                "its category without already knowing the brand name.\n"
                "Status: Every attempt to create fair test questions accidentally included the brand "
                "name or website, so we could not run an unbiased test."
            )
            plain_finding, format_generation_log = make_plain_finding(facts_summary, fallback)
            return DimensionScore(
                dimension=DIMENSION_NAME,
                score=-1,
                details={
                    "status": "insufficient_data",
                    "reason": (
                        f"Could not generate brand-free customer queries after {MAX_GENERATION_ATTEMPTS} attempts; "
                        "every generated batch contained the brand name/domain or was empty."
                    ),
                    "generation_attempts": generation_log,
                    "generation_log": format_generation_log,
                    "plain_finding": plain_finding,
                },
            )

        # Cache this domain's (category, queries) so the NEXT diagnosis run
        # reuses the exact same test instead of generating fresh wording —
        # this is the actual fix for run-to-run score instability.
        save_queries(domain, category, queries)

    domain_lower = domain.lower()
    company_lower = company_name.lower()

    runs_by_query = _run_queries_multiple_times(queries, domain_lower, company_lower)

    per_query_results = []
    raw_answers: dict[str, str] = {}
    fully_failed_queries = []

    for query in queries:
        runs = runs_by_query[query]
        successful_runs = [run for run in runs if run["status"] == "ok"]
        hits = sum(1 for run in successful_runs if run["is_hit"])
        # A question's mention rate is only ever computed from its own
        # successful runs; a failed call simply isn't counted either way,
        # it never counts as a miss.
        mention_rate = (hits / len(successful_runs)) if successful_runs else None

        per_query_results.append(
            {
                "query": query,
                "runs_per_query": RUNS_PER_QUERY,
                "runs": runs,
                "successful_runs": len(successful_runs),
                "failed_runs": RUNS_PER_QUERY - len(successful_runs),
                "mention_rate": mention_rate,
            }
        )

        if successful_runs:
            # One representative answer per query for topic_relevance,
            # which expects a single string per query (raw_answers is a
            # dict[query, answer_text]) — the first successful run's
            # answer, so that downstream shape never has to change.
            raw_answers[query] = next(run["answer_text"] for run in successful_runs)
        else:
            fully_failed_queries.append(query)

    valid_queries = [entry for entry in per_query_results if entry["mention_rate"] is not None]

    # One entry per successful answer, for build_source_map — computed
    # here regardless of outcome so it's available whether or not any
    # query ended up "valid" (it degrades to an empty-but-valid stats
    # shape on its own when there are no successful answers at all).
    answers_for_source_map = [
        {"query": entry["query"], "citations": run["all_citations"]}
        for entry in per_query_results
        for run in entry["runs"]
        if run["status"] == "ok"
    ]
    source_map = build_source_map(domain, answers_for_source_map)

    if not valid_queries:
        # Every single call failed across all queries and all runs
        # (network error, invalid key, etc.). We genuinely could not
        # determine anything real, so we do not report a numeric score.
        fallback = (
            "We had good customer questions ready to test"
            + (f', like "{queries[0]}",' if queries else "")
            + f" and asked each one {RUNS_PER_QUERY} separate times, but every single attempt to ask AI "
            "search tools those questions failed to get a response, so we don't have a real reading for "
            "this check. This is usually a temporary connection issue, not something wrong with the "
            "brand. Today, just try running the report again in a few minutes."
        )
        facts_summary = (
            "Dimension: how often AI search tools mention this company when a customer researches "
            "its category without already knowing the brand name.\n"
            f"Status: We had real customer test questions ready and asked each one {RUNS_PER_QUERY} "
            "times, but every attempt to get an answer from AI search tools failed this time."
        )
        plain_finding, format_generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": "All Perplexity requests failed; no real data was obtained.",
                "category": category,
                "queries": queries,
                "runs_per_query": RUNS_PER_QUERY,
                "per_query_results": per_query_results,
                "fully_failed_queries": fully_failed_queries,
                "source_map": source_map,
                "generation_log": format_generation_log,
                "plain_finding": plain_finding,
            },
        )

    mention_rates = [entry["mention_rate"] for entry in valid_queries]
    average_mention_rate = sum(mention_rates) / len(mention_rates)
    score = round(100 * average_mention_rate)

    fallback = _build_plain_finding_success(category, len(valid_queries), average_mention_rate, per_query_results)
    facts_summary = _build_facts_summary_success(category, len(valid_queries), average_mention_rate, per_query_results)
    plain_finding, format_generation_log = make_plain_finding(facts_summary, fallback)

    return DimensionScore(
        dimension=DIMENSION_NAME,
        score=score,
        details={
            "status": "ok",
            "category": category,
            "generation_attempts": generation_log,
            "runs_per_query": RUNS_PER_QUERY,
            "queries_total": len(queries),
            "queries_valid": len(valid_queries),
            "average_mention_rate": round(average_mention_rate, 3),
            "per_query_results": per_query_results,
            "fully_failed_queries": fully_failed_queries,
            "source_map": source_map,
            "raw_answers": raw_answers,
            "generation_log": format_generation_log,
            "plain_finding": plain_finding,
        },
    )
