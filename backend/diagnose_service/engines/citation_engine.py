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
   not the company's own marketing language, and compare it against the
   categories already known (temperature 0, with a stored reason). The
   same kind of product reuses the same category.
2. Each category has ONE frozen set of 5 brand-free buyer questions,
   stored in the committed state directory (see engines._question_sets).
   A new category's set is generated from the category description
   ALONE — see _call_deepseek_for_queries for why the homepage text is
   deliberately withheld — and automatically checked (brand-free, exact
   count, formatting, plus an LLM review of every question) before it is
   frozen. See generate_checked_question_set. A frozen set never expires
   and is never regenerated implicitly, so the test is the same on every
   machine and every day; companies in the same category are measured
   with the same questions and are directly comparable.
3. Query Perplexity with only those brand-free questions, and check
   whether the company or its domain shows up organically in the answer
   or citations.

Any generated batch of questions that contains a company name or domain
is never stored and never reaches Perplexity or the score.

Whether a mention "counts" is no longer a plain substring check (see
_company_name_mentioned and _domain_matches_citation): a substring match
on an ordinary English word used as a company name — "Close", "Linear" —
false-positives on completely unrelated sentences ("close the deal", "a
linear process"), which quietly inflated the mention rate for exactly
those companies. See _COMMON_WORD_COMPANY_NAMES for the list of names
held to a stricter bar.

That stricter bar went through one revision already. The first version
required the domain to ALSO appear in the answer text (or a citation to
match it) before a common-word name counted as a hit. Checked against
real data, that was too strict in the other direction: every one of a
sample of flipped cases was a genuine brand recommendation ("Close is a
strong fit for small sales teams...") with no domain anywhere in the
answer, so the rule was silently discarding real mentions, not just
"close the deal"-style noise. The current version instead: a
domain-matching citation is still an automatic hit (cheap, exact, no
LLM needed); otherwise every sentence containing the name is handed to
DeepSeek along with the real company name, domain, and category, asking
whether the sentence is actually about the company or just uses the
word in its ordinary sense (see _judge_common_word_mention). A claimed
"yes" is only trusted if the quoted evidence sentence is a real,
word-for-word substring of the actual answer text. If that disambiguation
call itself fails, the run is marked "uncertain" — not guessed at as
either a hit or a miss, and excluded from the mention-rate denominator
for that question (see check_citation_visibility).

Every returned DimensionScore also carries a `plain_finding` string: a
founder-readable explanation (what's wrong, why it happens in business
terms, and one concrete thing to do today) built only from this run's
real data. All the technical fields below it (category, hit_queries,
etc.) are kept as-is for debugging.
"""

import json
import re
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from shared.config import settings
from shared._fetch_utils import fetch_page_text
from engines._plain_finding_generator import make_plain_finding
from engines._provenance import page_fingerprint
from engines._question_sets import (
    create_category,
    get_companies_in_category,
    get_company_assignment,
    get_current_question_set,
    list_categories,
    normalize_domain,
    save_company_assignment,
)
from engines._source_map import _extract_domain, _is_own_domain, build_source_map
from engines._stats import wilson_interval
from shared._text_format import PLAIN_LANGUAGE_FORMAT_RULES, find_banned_formatting, sanitize_banned_formatting
from models import DimensionScore

DIMENSION_NAME = "citation_visibility"

# Bump whenever this dimension's measurement method changes in a way
# that makes results computed before and after not directly comparable.
METHOD_VERSION = "v1"

# Recorded on every stored question-set version. Bump when the
# generation/check method changes; existing frozen sets are NOT
# regenerated by a bump, only by backend/scripts/question_sets.py.
QUESTION_SET_METHOD_VERSION = "v1"

MAX_GENERATION_ATTEMPTS = 3

# Each of the 5 brand-free queries is sent to Perplexity this many times,
# since a single real-world call can hit or miss on its own regardless of
# genuine visibility; averaging across repeats turns that into an actual
# mention RATE per question instead of a one-shot coin flip.
RUNS_PER_QUERY = 3

# Upper bound on how many of the RUNS_PER_QUERY * REQUIRED_QUERY_COUNT
# Perplexity calls are in flight at once.
MAX_CONCURRENT_CALLS = 5

_CATEGORY_SYSTEM_PROMPT = f"""
You are a B2B market research analyst. You will be given the visible text
of a company's homepage.

Identify the type of product this company sells, as a customer-facing
category. Describe it the way an independent industry analyst or a
customer researching solutions would describe it, NOT using the company's
own marketing language, and NOT using the company's name or any other
brand name. Name the type of product, not this company's particular
positioning, so that other companies selling the same kind of product
would fall into the same category.

Respond with ONLY a JSON object, no other text, in this exact shape:
{{"category_name": "<2 to 6 word name of the product category>", "category_description": "<one sentence describing what this kind of product does and for whom>"}}

{PLAIN_LANGUAGE_FORMAT_RULES}
""".strip()

# Deliberately does NOT ask for or accept homepage text as input — see
# _call_deepseek_for_queries for why.
_QUERY_SYSTEM_PROMPT = f"""
You are a B2B market research analyst. You will be given a short
description of a customer-facing category or niche, with no company name
attached to it.

Generate exactly 5 realistic questions or search queries that a customer
in this category would type into an AI assistant BEFORE knowing about any
specific vendor. Each question must describe a problem, task, or need in
the category. They must NOT mention any specific company name, domain, or
product name — you were not given one, so do not invent one either.

Respond with ONLY a JSON object, no other text, in this exact shape:
{{"queries": ["<query 1>", "<query 2>", "<query 3>", "<query 4>", "<query 5>"]}}

{PLAIN_LANGUAGE_FORMAT_RULES}
""".strip()

_CATEGORY_MATCH_SYSTEM_PROMPT = f"""
You are a B2B market research analyst. You will be given the category of
a company that has not been categorized yet, and a list of existing
categories, each with an id, a name, and a description.

Decide whether the new company sells the SAME TYPE OF PRODUCT as one of
the existing categories: a customer shopping for one would compare it
directly against products in that category as alternatives for the same
need. Merely related, adjacent, or overlapping markets do not count.

Respond with ONLY a JSON object, no other text, in this exact shape:
{{"matched_category_id": "<the id of the matching existing category, or null if none matches>", "reason": "<one or two sentences explaining the decision>"}}

{PLAIN_LANGUAGE_FORMAT_RULES}
""".strip()

_QUESTION_REVIEW_SYSTEM_PROMPT = f"""
You are reviewing test questions for a market research study. You will
be given a product category description and a numbered list of
questions that are supposed to be what a real customer in this category
would type into an AI assistant BEFORE knowing about any specific vendor.

Judge each question separately. A question passes only if ALL of these
are true:
- A real buyer who does not know any vendor or brand would plausibly ask
  an AI assistant this question.
- It describes a problem, task, or need in this category.
- It does not mention or hint at any specific company, product, brand,
  or website.
- It is written from the buyer's point of view, not the vendor's.

Respond with ONLY a JSON object, no other text, in this exact shape:
{{"reviews": [{{"index": 1, "passes": true or false, "reason": "<one sentence>"}}]}}
with exactly one entry per question, using the question's number as index.

{PLAIN_LANGUAGE_FORMAT_RULES}
""".strip()

# Fixed at exactly 5 (not "3 to 5") so the "N out of M queries hit" ratio
# means the same thing on every run — a variable count would make scores
# from different runs incomparable even if visibility genuinely didn't change.
REQUIRED_QUERY_COUNT = 5

# Company names that are also ordinary English words. A plain whole-word
# match on one of these is not reliable evidence of a real brand mention
# — "close the deal", "a linear process" — so these names are held to a
# stricter bar (see _is_common_word_company_name and check_citation_visibility).
_COMMON_WORD_COMPANY_NAMES = {
    "close", "linear", "notion", "monday", "box", "front", "loom", "slack", "zoom",
}


def _is_common_word_company_name(company_name: str) -> bool:
    """True when `company_name` itself (case-insensitive, whole name) is one of _COMMON_WORD_COMPANY_NAMES."""
    return company_name.strip().lower() in _COMMON_WORD_COMPANY_NAMES


def _company_name_mentioned(company_name: str, answer_text: str) -> bool:
    """
    Whole-word, case-insensitive match for `company_name` inside
    `answer_text` — not a substring check, so a company named "Close" is
    not falsely matched by the word "close" inside "close the deal".
    Works for multi-word names ("Feroot Security") by requiring a word
    boundary only at the start and end of the whole phrase, not between
    its internal words (so the space between "Feroot" and "Security"
    matches literally, the way the name is actually written).
    """
    name = company_name.strip()
    if not name:
        return False
    pattern = r"\b" + re.escape(name) + r"\b"
    return bool(re.search(pattern, answer_text, re.IGNORECASE))


def _domain_matches_citation(citation_url: str, domain_lower: str) -> bool:
    """
    True only if `citation_url`'s own hostname equals `domain_lower` or
    is a real subdomain of it. Reuses engines._source_map's own
    domain-matching logic (hostname extraction + exact-or-subdomain
    check) instead of a substring check, so "notferoot.com" can never
    match "feroot.com" and "app.feroot.com" correctly does.
    """
    host = _extract_domain(citation_url)
    if host is None:
        return False
    return _is_own_domain(host, domain_lower)


def _find_sentences_mentioning_name(company_name: str, answer_text: str) -> list[str]:
    """
    Split `answer_text` into sentences and return every one containing a
    whole-word match of `company_name` — the candidate sentences a
    common-word company name needs an LLM judgment on, since the word
    merely appearing isn't enough evidence on its own (see
    _judge_common_word_mention).
    """
    name = company_name.strip()
    if not name:
        return []
    pattern = r"\b" + re.escape(name) + r"\b"
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer_text) if s.strip()]
    return [sentence for sentence in sentences if re.search(pattern, sentence, re.IGNORECASE)]


_COMMON_WORD_DISAMBIGUATION_SYSTEM_PROMPT = """
ROLE
You are checking whether a word, which also happens to be an ordinary English word, is being used in a piece of text to refer to a specific real company by that name, or is just being used as the ordinary English word with no connection to that company.

You will be given the company's real name, its website domain, and the general category it competes in, then one or more sentences from a real AI-generated answer, all containing that word. Decide whether the sentences are actually talking about the real company, or just happen to use the same word as an everyday word with an unrelated meaning.

Respond with ONLY a JSON object, no other text, no markdown formatting, no code fences, in this exact shape:
{"refers_to_company": true or false, "evidence_sentence": "<the exact sentence, copied word for word from the sentences you were given, that best supports your answer, or an empty string if refers_to_company is false>"}

If refers_to_company is true, evidence_sentence must be an exact, word-for-word copy of one of the sentences you were given — never invent, paraphrase, or combine sentences.
""".strip()


def _call_deepseek_for_common_word_disambiguation(
    company_name: str, domain: str, category: str, candidate_sentences: list[str]
) -> dict:
    """
    Ask DeepSeek whether `candidate_sentences` (each already confirmed to
    contain a whole-word match of `company_name`) are actually about the
    real company, given its name, domain, and category as context.
    Raises requests.RequestException on network/HTTP failure, ValueError
    if the response isn't valid JSON in the expected shape.
    """
    url = f"{settings.deepseek_api_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    user_content = (
        f"Company name: {company_name}\n"
        f"Company domain: {domain}\n"
        f"Category the company competes in: {category}\n\n"
        f'Sentences containing the word "{company_name}":\n'
        + "\n".join(f"- {sentence}" for sentence in candidate_sentences)
    )
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": _COMMON_WORD_DISAMBIGUATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
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
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    parsed = json.loads(cleaned)

    if "refers_to_company" not in parsed:
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")

    return parsed


def _judge_common_word_mention(company_name: str, domain: str, category: str, answer_text: str) -> dict:
    """
    For a common-word company name with no domain-matching citation,
    decide whether `answer_text` actually mentions the real company.

    No candidate sentences at all -> "no_mention", no LLM call. Otherwise
    asks DeepSeek (see _call_deepseek_for_common_word_disambiguation) and
    only trusts a "yes" if the evidence sentence it quotes is a real,
    word-for-word substring of `answer_text` — never a fabricated quote.
    An LLM call failure is caught and reported as "uncertain" rather than
    guessed at as either a hit or a miss.

    Returns {"classification": "hit" | "no_mention" | "uncertain",
    "candidate_sentences": [...], "llm_judgment": dict | None,
    "evidence_sentence": str | None}. Never raises.
    """
    candidate_sentences = _find_sentences_mentioning_name(company_name, answer_text)

    if not candidate_sentences:
        return {
            "classification": "no_mention",
            "candidate_sentences": [],
            "llm_judgment": None,
            "evidence_sentence": None,
        }

    try:
        judgment = _call_deepseek_for_common_word_disambiguation(company_name, domain, category, candidate_sentences)
    except (requests.RequestException, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        return {
            "classification": "uncertain",
            "candidate_sentences": candidate_sentences,
            "llm_judgment": None,
            "evidence_sentence": None,
            "error": str(exc),
        }

    refers_to_company = bool(judgment.get("refers_to_company"))
    evidence_sentence = (judgment.get("evidence_sentence") or "").strip()

    # The claimed evidence sentence must really appear in the real
    # answer text — never trust the model's own claim that a quote is
    # real without checking it.
    if refers_to_company and (not evidence_sentence or evidence_sentence not in answer_text):
        refers_to_company = False
        evidence_sentence = ""

    return {
        "classification": "hit" if refers_to_company else "no_mention",
        "candidate_sentences": candidate_sentences,
        "llm_judgment": judgment,
        "evidence_sentence": evidence_sentence or None,
    }


def _post_deepseek_json(system_prompt: str, user_content: str) -> dict:
    """
    One temperature-0 DeepSeek chat call that must answer with a JSON
    object. Raises requests.RequestException on network/HTTP failure,
    ValueError / json.JSONDecodeError if the reply isn't a JSON object.
    """
    url = f"{settings.deepseek_api_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
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
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError(f"DeepSeek response is not a JSON object: {parsed!r}")
    return parsed


def _call_deepseek_for_category(homepage_text: str) -> dict:
    """
    Ask DeepSeek to classify the company's buyer-facing category from
    its real homepage text. Returns {"name": str, "description": str}.
    Raises requests.RequestException on network/HTTP failure, ValueError
    if the response isn't valid JSON in the expected shape.
    """
    parsed = _post_deepseek_json(_CATEGORY_SYSTEM_PROMPT, homepage_text[:8000])
    name = (parsed.get("category_name") or "").strip()
    description = (parsed.get("category_description") or "").strip()
    if not name or not description:
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")
    return {"name": name, "description": description}


def _call_deepseek_for_category_match(classified: dict, existing_categories: list[dict]) -> dict:
    """
    Ask DeepSeek whether the newly classified category is the same kind
    of product as one of `existing_categories`. Returns
    {"matched_category_id": str | None, "reason": str}. A returned id
    that isn't one of the existing ids is treated as no match, never
    trusted as-is. Raises like _post_deepseek_json.
    """
    existing_lines = "\n".join(
        f'- id: {c["category_id"]} | name: {c["category_name"]} | description: {c["category_description"]}'
        for c in existing_categories
    )
    user_content = (
        f"NEW COMPANY CATEGORY\nname: {classified['name']}\ndescription: {classified['description']}\n\n"
        f"EXISTING CATEGORIES\n{existing_lines}"
    )
    parsed = _post_deepseek_json(_CATEGORY_MATCH_SYSTEM_PROMPT, user_content)
    if "matched_category_id" not in parsed or not (parsed.get("reason") or "").strip():
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")
    matched_id = parsed.get("matched_category_id")
    valid_ids = {c["category_id"] for c in existing_categories}
    reason = parsed["reason"].strip()
    if matched_id not in valid_ids:
        if matched_id:
            reason = f"{reason} (Returned id {matched_id!r} is not an existing category, treated as no match.)"
        matched_id = None
    return {"matched_category_id": matched_id, "reason": reason}


def _call_deepseek_for_queries(category_description: str) -> list[str]:
    """
    Ask DeepSeek to generate brand-free buyer queries for
    `category_description` ALONE. The real homepage text is deliberately
    never given to this call: handing it the homepage text let the model
    lift wording straight from the page, which biased the generated
    questions toward vocabulary this exact site already happens to use —
    making "does the site answer this question" an easier test to pass
    than a genuinely independent buyer question would be. Raises
    requests.RequestException on network/HTTP failure, ValueError if the
    response isn't valid JSON in the expected shape.
    """
    parsed = _post_deepseek_json(_QUERY_SYSTEM_PROMPT, category_description)
    if "queries" not in parsed or not isinstance(parsed["queries"], list):
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")
    return parsed["queries"]


def _call_deepseek_for_question_review(category_description: str, questions: list[str]) -> list[dict]:
    """
    Ask DeepSeek, question by question, whether each one reads like a
    real buyer who doesn't know any vendor would ask an AI assistant.
    Returns exactly len(questions) entries [{"passes": bool, "reason":
    str}] in question order. Raises ValueError if the reply doesn't
    cover every question exactly once with a reason.
    """
    numbered = "\n".join(f"{index}. {question}" for index, question in enumerate(questions, start=1))
    user_content = f"CATEGORY\n{category_description}\n\nQUESTIONS\n{numbered}"
    parsed = _post_deepseek_json(_QUESTION_REVIEW_SYSTEM_PROMPT, user_content)

    reviews = parsed.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")
    by_index = {}
    for review in reviews:
        if not isinstance(review, dict) or not isinstance(review.get("passes"), bool):
            raise ValueError(f"Malformed question review entry: {review!r}")
        reason = (review.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"Question review entry has no reason: {review!r}")
        by_index[review.get("index")] = {"passes": review["passes"], "reason": reason}
    if sorted(k for k in by_index if isinstance(k, int)) != list(range(1, len(questions) + 1)):
        raise ValueError(f"Question review does not cover every question exactly once: {parsed!r}")
    return [by_index[index] for index in range(1, len(questions) + 1)]


_DOMAIN_PATTERN = re.compile(r"\b[a-z0-9-]+\.(?:com|io|ai|app|co|net|org|dev|so|xyz|tech|cloud)\b", re.IGNORECASE)


def _deterministic_question_issues(question: str, brand_terms: list[dict]) -> tuple[list[str], list[str]]:
    """
    The rule-based checks for one question. Returns (blocking_issues,
    format_issues): a blocking issue (a known company's name or domain,
    or anything that looks like a domain) means the question must never
    reach Perplexity; a format issue (banned characters) can be fixed by
    deterministic sanitizing.
    """
    blocking = []
    question_lower = question.lower()
    for term in brand_terms:
        name = (term.get("company_name") or "").strip()
        domain = (term.get("domain") or "").strip().lower()
        if name and _company_name_mentioned(name, question):
            blocking.append(f"contains company name {name!r}")
        if domain and domain in question_lower:
            blocking.append(f"contains domain {domain!r}")
    if _DOMAIN_PATTERN.search(question):
        blocking.append("contains a website domain")
    return blocking, sorted(set(find_banned_formatting(question)))


def _check_question_batch(raw_queries: list[str], category_description: str, brand_terms: list[dict]) -> dict:
    """
    Run every automatic check on one generated batch and return the
    attempt record: per-question deterministic issues plus the LLM's
    pass/fail and reason, how many questions pass, and whether the whole
    batch passes. A batch that isn't exactly REQUIRED_QUERY_COUNT
    questions is not LLM-reviewed at all: it can never be used.
    """
    if len(raw_queries) != REQUIRED_QUERY_COUNT:
        return {
            "usable": False,
            "all_passed": False,
            "passed_count": 0,
            "substantive_passed_count": 0,
            "reason": f"Expected exactly {REQUIRED_QUERY_COUNT} queries, got {len(raw_queries)}",
            "questions": [{"question": q} for q in raw_queries],
        }

    reviews = _call_deepseek_for_question_review(category_description, raw_queries)
    per_question = []
    for question, review in zip(raw_queries, reviews):
        blocking, format_issues = _deterministic_question_issues(question, brand_terms)
        substantive_pass = not blocking and review["passes"]
        per_question.append(
            {
                "question": question,
                "blocking_issues": blocking,
                "format_issues": format_issues,
                "llm_passes": review["passes"],
                "llm_reason": review["reason"],
                "substantive_pass": substantive_pass,
                "passed": substantive_pass and not format_issues,
            }
        )

    return {
        "usable": not any(entry["blocking_issues"] for entry in per_question),
        "all_passed": all(entry["passed"] for entry in per_question),
        "passed_count": sum(1 for entry in per_question if entry["passed"]),
        "substantive_passed_count": sum(1 for entry in per_question if entry["substantive_pass"]),
        "questions": per_question,
    }


def generate_checked_question_set(category_description: str, brand_terms: list[dict]) -> tuple[dict | None, list[dict]]:
    """
    Generate a question set for a category from its description alone,
    and check it automatically: no brand name or domain, exactly
    REQUIRED_QUERY_COUNT questions, the formatting rules, and an LLM
    review of every question ("would a real buyer who knows no vendor
    ask an AI this?"). The first batch where every check passes is
    returned with status "frozen". Otherwise up to
    MAX_GENERATION_ATTEMPTS batches are tried, and the usable batch
    (right count, no brand or domain anywhere) with the most
    substantively passing questions is returned with status
    "needs_attention" (formatting issues are sanitized deterministically,
    as before). If even that is the full set, it is "frozen" after
    sanitizing, matching the old formatting rule.

    `brand_terms` is [{company_name, domain}] for every company that will
    use this set. Returns (version_record or None, attempts). None means
    no usable batch at all; nothing should be stored. Network/API errors
    propagate immediately (they are not retried as a quality problem).
    """
    attempts = []
    for attempt_number in range(1, MAX_GENERATION_ATTEMPTS + 1):
        raw_queries = [
            q.strip() for q in _call_deepseek_for_queries(category_description) if isinstance(q, str) and q.strip()
        ]
        attempt = {"attempt": attempt_number, **_check_question_batch(raw_queries, category_description, brand_terms)}
        attempts.append(attempt)
        if attempt["usable"] and attempt["all_passed"]:
            return _build_version_record([q["question"] for q in attempt["questions"]], "frozen", attempt_number, attempts), attempts

    usable = [a for a in attempts if a["usable"]]
    if not usable:
        return None, attempts

    best = max(usable, key=lambda a: (a["substantive_passed_count"], -a["attempt"]))
    questions = [sanitize_banned_formatting(q["question"]) for q in best["questions"]]
    status = "frozen" if best["substantive_passed_count"] == REQUIRED_QUERY_COUNT else "needs_attention"
    return _build_version_record(questions, status, best["attempt"], attempts), attempts


def _build_version_record(questions: list[str], status: str, chosen_attempt: int, attempts: list[dict]) -> dict:
    """The stored record for one question-set version (the version number itself is assigned on save)."""
    chosen = next(a for a in attempts if a["attempt"] == chosen_attempt)
    return {
        "questions": questions,
        "status": status,
        "generated_on": datetime.now(timezone.utc).date().isoformat(),
        "generation_method_version": QUESTION_SET_METHOD_VERSION,
        "model": settings.deepseek_model,
        "auto_check": {
            "chosen_attempt": chosen_attempt,
            "passed_count": chosen["passed_count"],
            "substantive_passed_count": chosen["substantive_passed_count"],
            "attempts": attempts,
        },
    }


def _brand_terms_for_category(category_id: str | None, company_name: str, domain: str) -> list[dict]:
    """Every company that uses (or is about to use) this category's questions, for the brand-free check."""
    terms = [{"company_name": company_name, "domain": normalize_domain(domain)}]
    if category_id:
        for company in get_companies_in_category(category_id):
            if company["domain"] != terms[0]["domain"]:
                terms.append(company)
    return terms


def _resolve_question_set(company_name: str, domain: str) -> tuple[dict | None, dict]:
    """
    Find (or, for a brand-new category, create) the frozen question set
    this company is measured with. Returns (question_set, log):
    question_set is get_current_question_set's shape plus "source", or
    None with log["failure"] explaining why ("no_deepseek_key",
    "homepage_fetch_failed", "homepage_empty", "llm_failed",
    "no_usable_questions").

    1. A company that was classified before reuses its stored category
       and that category's current version. No LLM call, no fetch.
    2. A new company is classified from its homepage text, then compared
       to every existing category by a temperature-0 LLM call that must
       give a reason. Same kind of product -> reuse that category's set.
    3. Otherwise a new category's set is generated and auto-checked (see
       generate_checked_question_set) from the description alone, then
       stored and frozen.
    """
    assignment = get_company_assignment(domain)
    if assignment:
        question_set = get_current_question_set(assignment["category_id"])
        if question_set:
            return {**question_set, "source": "existing_company_assignment"}, {"assignment": assignment}

    if not settings.has_deepseek_key():
        return None, {"failure": "no_deepseek_key"}

    try:
        homepage_text = fetch_page_text(domain)
    except requests.RequestException as exc:
        return None, {"failure": "homepage_fetch_failed", "error": str(exc)}
    if not homepage_text:
        return None, {"failure": "homepage_empty"}

    homepage_url = domain if domain.startswith("http") else f"https://{domain}"
    log: dict = {"page_fingerprints": [page_fingerprint(homepage_url, homepage_text)]}

    try:
        classified = _call_deepseek_for_category(homepage_text)
        classified = {key: sanitize_banned_formatting(value) for key, value in classified.items()}
        existing = list_categories()
        if existing:
            match = _call_deepseek_for_category_match(classified, existing)
        else:
            match = {"matched_category_id": None, "reason": "No existing categories to compare against yet."}

        category_id = match["matched_category_id"]
        source = "matched_existing_category"
        if category_id is None:
            version_record, attempts = generate_checked_question_set(
                classified["description"], _brand_terms_for_category(None, company_name, domain)
            )
            log["generation_attempts"] = attempts
            if version_record is None:
                return None, {**log, "failure": "no_usable_questions", "classified_as": classified}
            category_id = create_category(classified["name"], classified["description"], version_record)
            source = "new_category"
    except (requests.RequestException, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
        return None, {**log, "failure": "llm_failed", "error": str(exc)}

    save_company_assignment(
        domain,
        {
            "category_id": category_id,
            "company_name": company_name,
            "assigned_on": datetime.now(timezone.utc).date().isoformat(),
            "classified_as": classified,
            "match_decision": {
                "matched_existing": source == "matched_existing_category",
                "reason": match["reason"],
            },
            "homepage_text_sha256": log["page_fingerprints"][0]["text_sha256"],
        },
    )
    # Re-read: if a concurrent diagnosis of the same domain stored its
    # assignment first, that one wins and this run uses it too.
    stored = get_company_assignment(domain)
    question_set = get_current_question_set(stored["category_id"])
    return {**question_set, "source": source}, log


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


def _execute_single_run(
    query: str, domain: str, company_name: str, category: str, name_is_common_word: bool
) -> dict:
    """
    One Perplexity call for `query`.

    Non-common-word names: a whole-word name match
    (_company_name_mentioned) or a domain-matching citation
    (_domain_matches_citation) is enough on its own, no LLM call —
    unchanged, see this module's docstring.

    Common-word names (see _COMMON_WORD_COMPANY_NAMES): a
    domain-matching citation is still an automatic hit, no LLM call.
    Otherwise a name match alone is NOT trusted — see
    _judge_common_word_mention for the candidate-sentence + LLM
    disambiguation this falls back to, and why.

    Every result carries `mention_rule` (which of the three paths above
    decided it: "whole_word", "citation_domain_match", or
    "llm_disambiguation") and `mention_classification` ("hit",
    "no_mention", or "uncertain" — only reachable via
    llm_disambiguation). `is_hit` is True/False, except None when
    mention_classification is "uncertain": that run is deliberately
    neither a hit nor a miss (see check_citation_visibility, which
    excludes it from the mention-rate denominator).

    Never raises: a Perplexity request failure is reported as status
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
    domain_lower = domain.lower()
    matched_citations = [c for c in citations if _domain_matches_citation(c, domain_lower)]

    base_result = {
        "status": "ok",
        "answer_text": answer_text,
        "all_citations": citations,
        "matched_citations": matched_citations,
    }

    if not name_is_common_word:
        company_mentioned_in_answer = _company_name_mentioned(company_name, answer_text)
        is_hit = bool(matched_citations) or company_mentioned_in_answer
        return {
            **base_result,
            "mention_rule": "whole_word",
            "mention_classification": "hit" if is_hit else "no_mention",
            "candidate_sentences": [],
            "llm_judgment": None,
            "evidence_sentence": None,
            "is_hit": is_hit,
        }

    if matched_citations:
        return {
            **base_result,
            "mention_rule": "citation_domain_match",
            "mention_classification": "hit",
            "candidate_sentences": [],
            "llm_judgment": None,
            "evidence_sentence": None,
            "is_hit": True,
        }

    disambiguation = _judge_common_word_mention(company_name, domain, category, answer_text)
    classification = disambiguation["classification"]
    is_hit = None if classification == "uncertain" else classification == "hit"

    return {
        **base_result,
        "mention_rule": "llm_disambiguation",
        "mention_classification": classification,
        "candidate_sentences": disambiguation["candidate_sentences"],
        "llm_judgment": disambiguation["llm_judgment"],
        "evidence_sentence": disambiguation["evidence_sentence"],
        "is_hit": is_hit,
    }


def _run_queries_multiple_times(
    queries: list[str], domain: str, company_name: str, category: str, name_is_common_word: bool
) -> dict[str, list[dict]]:
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
        result = _execute_single_run(query, domain, company_name, category, name_is_common_word)
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


QUESTION_SET_NEEDS_ATTENTION_NOTE = (
    "Question set quality pending review: no generated batch passed every automatic check, so the "
    "batch with the most passing questions is being used."
)


def _question_set_failure_result(domain: str, resolve_log: dict) -> DimensionScore:
    """The insufficient_data DimensionScore for each way _resolve_question_set can fail."""
    failure = resolve_log.get("failure")
    dimension_note = (
        "Dimension: how often AI search tools mention this company when a customer researches its "
        "category without already knowing the brand name.\n"
    )

    if failure == "no_deepseek_key":
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

    if failure == "homepage_fetch_failed":
        fallback = (
            f"We couldn't open the homepage at {domain} at all, so there was nothing real to check "
            "this brand's category against, and no fair test could be built. This usually means "
            "the site blocked our visit or was temporarily down when we checked. Today, try "
            "loading the homepage yourself in an incognito tab to confirm it's reachable."
        )
        facts_summary = dimension_note + (
            f"Status: We could not open the homepage at {domain} to figure out what category to "
            "test. It may be blocked or temporarily down."
        )
        reason = f"Failed to fetch homepage for '{domain}'"
    elif failure == "homepage_empty":
        fallback = (
            f"We could open the homepage at {domain}, but found no real readable text on it. "
            "The visible copy your customers see may be loaded in a way our simple check can't "
            "read. Without real homepage text, we can't tell what category to test AI answers "
            "against. Today, check that your homepage's core copy shows up in the very first "
            "version of the page a visitor's browser receives, not just after extra loading."
        )
        facts_summary = dimension_note + f"Status: We opened the homepage at {domain} but found no readable text on it."
        reason = f"Homepage for '{domain}' returned no extractable body text (possibly JS-rendered)."
    elif failure == "no_usable_questions":
        fallback = (
            "Every time we asked our tool to come up with customer questions about your "
            "category, the questions either named a brand or website, or came back in the wrong "
            "number. That would make the test meaningless, like grading a quiz with the answer "
            "printed on the question. We refused to run the test on unfair questions, so there's "
            "no real score this time. Today, just try re-running the report."
        )
        facts_summary = dimension_note + (
            "Status: Every attempt to create fair test questions accidentally included a brand "
            "name or website, so we could not run an unbiased test."
        )
        reason = (
            f"Could not generate a usable question set after {MAX_GENERATION_ATTEMPTS} attempts; "
            "every generated batch contained a brand name/domain or had the wrong number of questions."
        )
    else:
        fallback = (
            "We hit a technical hiccup while working out what your customers search for before "
            "they know your brand, so there's no real test to report on this run. This is "
            "usually temporary. Today, just try running the report again."
        )
        facts_summary = dimension_note + "Status: A temporary technical problem stopped this check from completing."
        reason = f"DeepSeek category/question-set step failed: {resolve_log.get('error')}"

    plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
    details = {
        "status": "insufficient_data",
        "reason": reason,
        "generation_log": generation_log,
        "plain_finding": plain_finding,
    }
    if "generation_attempts" in resolve_log:
        details["generation_attempts"] = resolve_log["generation_attempts"]
    if resolve_log.get("page_fingerprints"):
        details["page_fingerprints"] = resolve_log["page_fingerprints"]
    return DimensionScore(dimension=DIMENSION_NAME, score=-1, details=details)


def check_citation_visibility(company_name: str, domain: str) -> DimensionScore:
    """
    Classify the company's buyer-facing category from its own homepage,
    generate brand-free buyer questions for that category, query
    Perplexity with them, and check whether the company or its domain
    shows up organically in the answer or citations.

    The questions come from the company's category's frozen question
    set (see _resolve_question_set): regenerating fresh wording on every
    run was the root cause of citation_visibility swinging wildly
    between back-to-back runs, since different wording produces
    different Perplexity searches. A company that was classified before
    skips both the homepage fetch and every DeepSeek generation call.

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

    question_set, resolve_log = _resolve_question_set(company_name, domain)

    if question_set is None:
        return _question_set_failure_result(domain, resolve_log)

    category = question_set["category_name"]
    category_description = question_set["category_description"]
    queries = list(question_set["questions"])
    question_set_info = {
        "category_id": question_set["category_id"],
        "category_name": category,
        "version": question_set["version"],
        "status": question_set["status"],
        "source": question_set["source"],
    }
    if question_set["status"] == "needs_attention":
        question_set_info["note"] = QUESTION_SET_NEEDS_ATTENTION_NOTE
    # A reused set was checked against the companies known when it was
    # generated; flag (never silently alter) any question that happens
    # to contain THIS company's name or domain.
    brand_hits = [
        q for q in queries if _deterministic_question_issues(q, [{"company_name": company_name, "domain": normalize_domain(domain)}])[0]
    ]
    if brand_hits:
        question_set_info["brand_warning"] = brand_hits
    generation_log = [{"outcome": question_set["source"], **resolve_log}]
    page_fingerprints = resolve_log.get("page_fingerprints", [])


    name_is_common_word = _is_common_word_company_name(company_name)

    runs_by_query = _run_queries_multiple_times(queries, domain, company_name, category_description, name_is_common_word)

    per_query_results = []
    raw_answers: dict[str, str] = {}
    fully_failed_queries = []
    total_uncertain_runs = 0
    # Pooled over every countable answer, for the mention-rate interval.
    total_mentions = 0
    total_countable = 0

    for query in queries:
        runs = runs_by_query[query]
        successful_runs = [run for run in runs if run["status"] == "ok"]
        # "uncertain" runs (the common-word LLM disambiguation call
        # itself failed) are neither a hit nor a miss — excluded from
        # both the hit count and the denominator, never guessed at
        # either way. See _execute_single_run / _judge_common_word_mention.
        uncertain_runs = [run for run in successful_runs if run["mention_classification"] == "uncertain"]
        countable_runs = [run for run in successful_runs if run["mention_classification"] != "uncertain"]
        total_uncertain_runs += len(uncertain_runs)
        hits = sum(1 for run in countable_runs if run["is_hit"])
        total_mentions += hits
        total_countable += len(countable_runs)
        # A question's mention rate is only ever computed from its own
        # countable runs; a failed or uncertain call simply isn't
        # counted either way, it never counts as a miss.
        mention_rate = (hits / len(countable_runs)) if countable_runs else None

        per_query_results.append(
            {
                "query": query,
                "runs_per_query": RUNS_PER_QUERY,
                "runs": runs,
                "successful_runs": len(successful_runs),
                "failed_runs": RUNS_PER_QUERY - len(successful_runs),
                "uncertain_runs": len(uncertain_runs),
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

    # Wilson 95% interval on the pooled mention rate: with only ~15
    # answers, "20%" really means "somewhere around 7% to 45%".
    interval = wilson_interval(total_mentions, total_countable)
    mention_interval = {
        "mentions": total_mentions,
        "total": total_countable,
        "ci_low": round(interval[0], 3) if interval else None,
        "ci_high": round(interval[1], 3) if interval else None,
        "ci_method": "Wilson score interval, z = 1.96",
    }

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
                "question_set": question_set_info,
                "queries": queries,
                "runs_per_query": RUNS_PER_QUERY,
                "name_is_common_word": name_is_common_word,
                "total_uncertain_runs": total_uncertain_runs,
                **mention_interval,
                "per_query_results": per_query_results,
                "fully_failed_queries": fully_failed_queries,
                "source_map": source_map,
                "page_fingerprints": page_fingerprints,
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
            "question_set": question_set_info,
            "generation_attempts": generation_log,
            "runs_per_query": RUNS_PER_QUERY,
            "queries_total": len(queries),
            "queries_valid": len(valid_queries),
            "average_mention_rate": round(average_mention_rate, 3),
            "name_is_common_word": name_is_common_word,
            "total_uncertain_runs": total_uncertain_runs,
            **mention_interval,
            "per_query_results": per_query_results,
            "fully_failed_queries": fully_failed_queries,
            "source_map": source_map,
            "raw_answers": raw_answers,
            "page_fingerprints": page_fingerprints,
            "generation_log": format_generation_log,
            "plain_finding": plain_finding,
        },
    )
