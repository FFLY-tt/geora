"""
Topic relevance engine.

Checks whether the customer's OWN website directly answers the real
questions a buyer would ask before purchasing — not whether an AI
assistant's answer happened to stay on topic. The previous version of
this engine measured word overlap between a question and whichever
sentence of an AI answer mentioned the company; that is a measurement
of the AI's answer, not of the customer's site, and is meaningless on
the (common) case where the company was never mentioned at all.

Reuses the 5 brand-free buyer questions citation_engine already
generated (see citation_engine.py) rather than generating new ones:
those questions are the product's definition of "what a buyer asks",
and regenerating a second set here would just be a different, harder
to compare test. The questions are pulled from
citation_result.details["per_query_results"], which exists whenever
question generation itself succeeded — independent of whether
citation_visibility's own score ended up -1 for an unrelated reason,
such as every Perplexity call failing. That independence matters: this
engine never needs AI answers to exist, only the homepage and a
DeepSeek judgment call.

For each question, DeepSeek judges the homepage plus up to 3 same-domain
pages most likely to cover it (features/product/solutions/pricing/
faq/how-it-works) and classifies the real website content as
"answered", "partial", or "missing". Classification runs 3 times at
temperature 0 and takes the majority (a 3-way split counts as
"partial"), and any claimed evidence sentence is checked against the
actual fetched page text — a quote DeepSeek can't back up with a real
sentence is treated as fabricated and downgraded to "missing".

backend/scripts/stability_audit.py proved that even with byte-identical
page content, this judgment call is itself not perfectly consistent —
the exact same (question, page text) input sent repeatedly came back
with a different classification on some runs. Each question's final,
already-majority-voted result is cached by engines._judgment_cache
(shared, committed state under state/judgments/),
keyed on the question plus the exact page text judged (never raw
HTML), so a repeated diagnosis against unchanged pages returns the
identical classification every time with no further DeepSeek calls —
see that module's docstring, and PROMPT_VERSION below for how a prompt
change invalidates old cache entries.
"""

import json
import re
from collections import Counter
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from shared.config import settings
from shared._fetch_utils import extract_text_from_html, fetch_page_text
from engines._judgment_cache import get_cached_judgment, make_judgment_cache_key, save_judgment
from engines._plain_finding_generator import make_plain_finding
from engines._provenance import page_fingerprint
from models import DimensionScore

DIMENSION_NAME = "topic_relevance"

# Bump whenever this dimension's measurement method changes in a way
# that makes results computed before and after not directly comparable.
METHOD_VERSION = "v1"

# Bump this whenever _RELEVANCE_JUDGE_SYSTEM_PROMPT's wording changes
# in a way that could make a previously-cached judgment unrepresentative
# of the new prompt. Folded into the judgment cache key.
PROMPT_VERSION = "v1"

# Up to this many same-domain pages beyond the homepage are read, so the
# judgment isn't limited to whatever happens to be on the homepage alone.
MAX_ADDITIONAL_PAGES = 3

# Link text/URL path keywords that mark a page as likely to actually
# cover a buyer's pre-purchase question, preferred over an arbitrary
# same-domain link.
_IMPORTANT_PAGE_KEYWORDS = ["features", "product", "solutions", "pricing", "faq", "how-it-works"]

# Each question is judged this many times at temperature 0, since even
# temperature 0 isn't perfectly deterministic; the majority classification
# across these runs is what actually counts (see _majority_classification).
JUDGMENT_RUNS_PER_QUESTION = 3

# Each page's text is truncated to this many characters before being
# handed to DeepSeek, so MAX_ADDITIONAL_PAGES + 1 pages together stay a
# reasonable request size.
MAX_CHARS_PER_PAGE = 3000

_RELEVANCE_JUDGE_SYSTEM_PROMPT = """
ROLE
You are judging whether a company's own website content directly answers one specific question a prospective customer would ask before buying. You will be given the question, and the real visible text of several pages copied from the company's own website, each page marked with its URL.

TASK
Classify the website content as exactly one of:
"answered": the website states a clear, direct answer to the question.
"partial": the website covers related ground but never states a clear, direct answer.
"missing": the website says nothing relevant to the question.

If your classification is "answered" or "partial", you must quote the exact sentence from the given page text that supports your judgment, copied exactly as it appears, word for word, with no changes, and say which page URL it came from. If your classification is "missing", leave the quote and URL as empty strings. Never quote a sentence that is not actually present in the page text you were given.

Respond with ONLY a JSON object, no other text, no markdown formatting, no code fences, in this exact shape:
{"classification": "answered", "evidence_sentence": "<exact sentence copied from the page text, or empty string>", "source_url": "<the page URL the sentence came from, or empty string>"}
""".strip()


def _extract_questions_from_citation_result(citation_result: DimensionScore) -> list[str] | None:
    """
    Pull the 5 brand-free questions citation_engine already generated,
    from details.per_query_results. That field exists whenever question
    generation itself succeeded, even if citation_visibility's own
    score ended up -1 for an unrelated reason (e.g. every Perplexity
    call then failed). Returns None only if no questions were ever
    generated at all.
    """
    per_query_results = citation_result.details.get("per_query_results")
    if not per_query_results:
        return None
    questions = [entry["query"] for entry in per_query_results if entry.get("query")]
    return questions or None


def _fetch_homepage_response(domain: str) -> requests.Response:
    """Fetch `domain`'s homepage and return the raw response (URL + HTML). Raises requests.RequestException on failure."""
    full_url = domain if domain.startswith("http") else f"https://{domain}"
    headers = {"User-Agent": settings.http_user_agent}
    response = requests.get(full_url, headers=headers, timeout=settings.request_timeout_seconds)
    response.raise_for_status()
    return response


def _normalize_url(url: str) -> str:
    """
    Canonicalize a URL for deduplication only (never used as the actual
    URL to fetch): forces https, drops a leading "www.", drops the
    fragment, and drops a trailing slash from the path. Without this,
    the same page reached through two different URL forms (e.g.
    "https://www.example.com/" vs "https://example.com") would be
    counted as two distinct pages, wasting one of the limited
    MAX_ADDITIONAL_PAGES slots re-reading a page already covered by the
    homepage (or by another link to it).
    """
    if not url.startswith("http"):
        url = f"https://{url}"
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[len("www."):]
    path = parsed.path.rstrip("/")
    return f"https://{netloc}{path}"


def _select_additional_pages(soup: BeautifulSoup, base_url: str) -> list[str]:
    """
    Pick up to MAX_ADDITIONAL_PAGES same-domain links from the homepage,
    preferring link text/URL paths that match _IMPORTANT_PAGE_KEYWORDS —
    the pages most likely to actually answer a buyer's pre-purchase
    question — before falling back to other same-domain links.

    Dedup (including against the homepage itself) is done on each
    link's normalized form, so a link that's actually the same page as
    one already picked never consumes a slot — that slot instead goes
    to the next distinct candidate link, since truncation to
    MAX_ADDITIONAL_PAGES happens only after this full pass.
    """
    base_host = urlparse(base_url).netloc

    preferred: list[str] = []
    other: list[str] = []
    seen_normalized: set[str] = {_normalize_url(base_url)}

    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).split("#")[0]
        parsed = urlparse(href)
        if parsed.netloc != base_host:
            continue  # only same-domain pages
        normalized = _normalize_url(href)
        if normalized in seen_normalized:
            continue  # skip the homepage itself and any page already picked, in any URL form
        seen_normalized.add(normalized)

        text = a.get_text(strip=True).lower()
        href_lower = href.lower()
        is_preferred = any(keyword in text or keyword in href_lower for keyword in _IMPORTANT_PAGE_KEYWORDS)
        (preferred if is_preferred else other).append(href)

    return (preferred + other)[:MAX_ADDITIONAL_PAGES]


def _build_pages_text(pages: list[tuple[str, str]]) -> str:
    """pages: [(url, text), ...], each already truncated to MAX_CHARS_PER_PAGE. Joined with a clear URL marker per page."""
    blocks = [f"=== Page: {url} ===\n{text}" for url, text in pages]
    return "\n\n".join(blocks)


def _call_deepseek_for_relevance_judgment(question: str, pages_text: str) -> dict:
    """
    Ask DeepSeek to classify whether the given pages answer `question`.
    Raises requests.RequestException on network/HTTP failure, ValueError
    if the response isn't valid JSON in the expected shape.
    """
    url = f"{settings.deepseek_api_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    user_content = f"Question: {question}\n\nWebsite pages:\n\n{pages_text}"
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": _RELEVANCE_JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
    }
    response = requests.post(url, headers=headers, json=payload, timeout=settings.request_timeout_seconds)
    response.raise_for_status()
    raw_response = response.json()

    content = raw_response["choices"][0]["message"]["content"]
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    parsed = json.loads(cleaned)

    if "classification" not in parsed:
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")

    return parsed


def _evidence_verified(evidence_sentence: str, source_url: str, page_texts_by_url: dict[str, str]) -> bool:
    """
    True only if `evidence_sentence` is a real substring of the exact
    page text we actually sent DeepSeek for `source_url`. Never trust
    the model's own claim that a quote is real without checking it
    against the real fetched text — this is what stops a fabricated
    quote from ever reaching a founder-facing report.
    """
    if not evidence_sentence or not source_url:
        return False
    page_text = page_texts_by_url.get(source_url)
    if not page_text:
        return False
    return evidence_sentence.strip() in page_text


def _majority_classification(classifications: list[str]) -> str:
    """
    Majority vote across however many judgment runs actually succeeded
    (normally JUDGMENT_RUNS_PER_QUESTION, fewer if some calls failed).
    If no single classification has strictly more votes than the
    runner-up — including a 3-way split where all runs disagree, or a
    1-1 split with only 2 successful runs — the result is "partial":
    repeated judgments disagreeing is itself a sign the website's
    answer is ambiguous at best, never confidently "answered" or
    cleanly "missing".
    """
    ranked = Counter(classifications).most_common()
    if len(ranked) == 1:
        return ranked[0][0]
    (top_classification, top_count), (_, second_count) = ranked[0], ranked[1]
    if top_count > second_count:
        return top_classification
    return "partial"


def _judge_question(question: str, pages_text: str, page_texts_by_url: dict[str, str]) -> dict:
    """
    Classify whether the website answers `question`, from
    JUDGMENT_RUNS_PER_QUESTION repeated DeepSeek calls. Never raises: a
    DeepSeek call failure for one run is recorded and simply excluded
    from the vote, so a bad run never fails the whole question, and a
    question where every run fails is reported with
    generation_failed=True instead of a fabricated classification.

    Checks engines._judgment_cache first, keyed on the question plus
    the exact page text being judged (see module docstring); a cache
    hit skips every DeepSeek call for this question entirely. Only a
    real (non-generation_failed) result is ever written to the cache.
    """
    cache_input = f"QUESTION: {question}\n\nPAGES:\n{pages_text}"
    cache_key = make_judgment_cache_key(DIMENSION_NAME, PROMPT_VERSION, settings.deepseek_model, cache_input)
    cached = get_cached_judgment(DIMENSION_NAME, cache_key)
    if cached is not None:
        return {**cached, "cache_status": "cache_hit"}

    runs = []
    for run_index in range(1, JUDGMENT_RUNS_PER_QUESTION + 1):
        try:
            parsed = _call_deepseek_for_relevance_judgment(question, pages_text)
        except (requests.RequestException, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
            runs.append({"run": run_index, "status": "error", "error": str(exc)})
            continue

        classification = parsed.get("classification")
        evidence_sentence = parsed.get("evidence_sentence") or ""
        source_url = parsed.get("source_url") or ""

        if classification not in ("answered", "partial", "missing"):
            runs.append({"run": run_index, "status": "error", "error": f"Unexpected classification: {classification!r}"})
            continue

        evidence_verified = True
        if classification in ("answered", "partial"):
            evidence_verified = _evidence_verified(evidence_sentence, source_url, page_texts_by_url)
            if not evidence_verified:
                # DeepSeek claimed a quote that isn't actually on the
                # page it named — treat this run as having found nothing
                # real, rather than trusting an unverifiable claim.
                classification = "missing"

        runs.append(
            {
                "run": run_index,
                "status": "ok",
                "classification": classification,
                "evidence_sentence": evidence_sentence if evidence_verified and classification != "missing" else None,
                "source_url": source_url if evidence_verified and classification != "missing" else None,
                "evidence_verified": evidence_verified,
            }
        )

    successful_runs = [run for run in runs if run["status"] == "ok"]

    if not successful_runs:
        return {
            "question": question,
            "classification": "missing",
            "evidence_sentence": None,
            "source_url": None,
            "runs": runs,
            "generation_failed": True,
            "cache_status": "fresh_judgment",
        }

    majority = _majority_classification([run["classification"] for run in successful_runs])
    # Use the evidence from a successful run that actually matches the
    # majority classification, so the quote shown lines up with the
    # final verdict rather than an outvoted minority run. When the
    # majority is a synthetic "partial" from a tie/3-way split with no
    # run that itself said "partial", there is no real evidence to
    # attach, and these stay None.
    matching_run = next((run for run in successful_runs if run["classification"] == majority), None)

    result = {
        "question": question,
        "classification": majority,
        "evidence_sentence": matching_run["evidence_sentence"] if matching_run else None,
        "source_url": matching_run["source_url"] if matching_run else None,
        "runs": runs,
        "generation_failed": False,
    }
    save_judgment(DIMENSION_NAME, cache_key, result)
    return {**result, "cache_status": "fresh_judgment"}


def check_topic_relevance(domain: str, citation_result: DimensionScore) -> DimensionScore:
    """
    Check whether the customer's OWN website directly answers the 5
    brand-free buyer questions citation_engine already generated.

    Returns score == -1 ("insufficient data") when citation_engine
    never generated any questions at all, when DeepSeek isn't
    configured, or when the homepage itself cannot be fetched. Never
    fabricates a score in those cases.
    """
    questions = _extract_questions_from_citation_result(citation_result)

    if not questions:
        citation_reason = citation_result.details.get("reason") or "no customer questions were generated."
        fallback = (
            "We couldn't check whether your own website answers real customer questions, because the "
            "earlier step that comes up with those questions didn't produce any this time. Today, just "
            "try running the report again."
        )
        facts_summary = (
            "Dimension: whether the company's OWN website directly answers the real questions a "
            "customer would ask before buying.\n"
            "Status: The earlier step that generates real customer questions did not produce any this "
            "time, so there is nothing to check this website's content against yet."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": f"No customer questions available from citation_engine: {citation_reason}",
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
                "reason": "DEEPSEEK_API_KEY is not configured; cannot judge whether the website answers these questions.",
                "plain_finding": (
                    "We couldn't check whether your own website answers real customer questions, "
                    "because the tool that reads your pages and judges the content isn't turned on "
                    "yet. Today, turn on that connection so this check can run for real next time."
                ),
            },
        )

    try:
        homepage_response = _fetch_homepage_response(domain)
    except requests.RequestException as exc:
        fallback = (
            f"We couldn't open the homepage at {domain} to check whether it directly answers the real "
            "questions a customer would ask before buying, so there's nothing real to report here. "
            "Today, try loading the homepage yourself to confirm it's reachable."
        )
        facts_summary = (
            "Dimension: whether the company's OWN website directly answers the real questions a "
            "customer would ask before buying.\n"
            f"Status: We could not open the homepage at {domain} to check this. It may be blocked or "
            "temporarily down."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": f"Failed to fetch homepage for '{domain}': {exc}",
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    homepage_url = homepage_response.url
    soup = BeautifulSoup(homepage_response.text, "html.parser")
    additional_urls = _select_additional_pages(soup, homepage_url)
    homepage_text = extract_text_from_html(homepage_response.text, method="main")[:MAX_CHARS_PER_PAGE]

    pages: list[tuple[str, str]] = [(homepage_url, homepage_text)]
    pages_read = [homepage_url]
    pages_failed = []

    for url in additional_urls:
        try:
            text = fetch_page_text(url, method="main")
        except requests.RequestException as exc:
            pages_failed.append({"url": url, "error": str(exc)})
            continue
        pages.append((url, text[:MAX_CHARS_PER_PAGE]))
        pages_read.append(url)

    page_texts_by_url = dict(pages)
    pages_text = _build_pages_text(pages)

    per_question_results = [_judge_question(question, pages_text, page_texts_by_url) for question in questions]

    answered_count = sum(1 for result in per_question_results if result["classification"] == "answered")
    partial_count = sum(1 for result in per_question_results if result["classification"] == "partial")
    total_questions = len(per_question_results)
    score = round((answered_count * 2 + partial_count) / (total_questions * 2) * 100)

    unanswered_questions = [
        result["question"] for result in per_question_results if result["classification"] != "answered"
    ]

    fallback = _build_fallback_plain_finding(per_question_results)
    facts_summary = _build_facts_summary(per_question_results)
    plain_finding, generation_log = make_plain_finding(facts_summary, fallback)

    return DimensionScore(
        dimension=DIMENSION_NAME,
        score=score,
        details={
            "status": "ok",
            "pages_read": pages_read,
            "pages_failed": pages_failed,
            "page_fingerprints": [page_fingerprint(url, text) for url, text in pages],
            "per_question_results": per_question_results,
            "unanswered_questions": unanswered_questions,
            "generation_log": generation_log,
            "plain_finding": plain_finding,
        },
    )


def _build_facts_summary(per_question_results: list) -> str:
    """Plain-text real facts handed to the AI writer — see engines._plain_finding_generator."""
    total = len(per_question_results)
    answered = [r["question"] for r in per_question_results if r["classification"] == "answered"]
    partial = [r["question"] for r in per_question_results if r["classification"] == "partial"]
    missing = [r["question"] for r in per_question_results if r["classification"] == "missing"]

    lines = [
        "Dimension: whether the company's OWN website directly answers the real questions a customer "
        "would ask before buying, not whether an AI assistant's answer happened to stay on topic.",
        f"Out of {total} real customer questions, the website directly and clearly answered "
        f"{len(answered)} of them.",
    ]
    if partial:
        quoted = "; ".join(f'"{q}"' for q in partial)
        lines.append(f"{len(partial)} question(s) are only partly covered, with no clear direct answer: {quoted}")
    if missing:
        quoted = "; ".join(f'"{q}"' for q in missing)
        lines.append(f"{len(missing)} question(s) are not addressed anywhere on the site at all: {quoted}")
    return "\n".join(lines)


def _build_fallback_plain_finding(per_question_results: list) -> str:
    """Founder-readable finding built only from this run's real judgments, used if AI writing fails."""
    unanswered = [r["question"] for r in per_question_results if r["classification"] != "answered"]

    if not unanswered:
        return (
            "Your own website directly answers every one of the real questions a customer would ask "
            "before buying. Today, just keep this content up to date as the product changes."
        )

    example = unanswered[0]
    return (
        f"Your own website does not clearly answer {len(unanswered)} of the real questions a customer "
        f'would ask before buying, including "{example}". When a customer, or an AI assistant acting on '
        "their behalf, looks for that answer on your site, it is not there in a clear, direct form, so "
        "they are left to guess or go check a competitor instead. Today, add a short, direct answer to "
        "that question on a page it makes sense for, like a FAQ or pricing page."
    )
