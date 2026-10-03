"""
Answer Capsule generation: for a set of brand-free customer questions,
either confirm the site already has a self-contained answer, or write
one by rewriting real page content, never inventing new facts.

Deliberately a separate module from technical_package_engine.py: that
module is pure rule-based generation with no external-API failure mode,
this one calls DeepSeek per question and has real failure modes (slow,
times out, errors). If this module has a bad day, the technical package
endpoint must be unaffected — see server.py, where they are two
separate routes for exactly this reason.

This service does not import anything from diagnose_service: the
brand-free question generation logic below is adapted from
diagnose_service/engines/citation_engine.py but copied and simplified
here on purpose, so agent_service has no dependency on diagnose_service
internals and can evolve independently.
"""

import json
import re

import requests

from shared._fetch_utils import fetch_page_text
from shared._text_format import PLAIN_LANGUAGE_FORMAT_RULES, find_banned_formatting, sanitize_banned_formatting
from shared.config import settings

REQUIRED_QUESTION_COUNT = 5
MAX_GENERATION_ATTEMPTS = 3

# A chunk of real page text needs at least this many words to plausibly
# read as a "self-contained answer" on its own, out of context.
MIN_SELF_CONTAINED_WORDS = 25
# Jaccard word-overlap between a question and a chunk at or above this
# counts as "this chunk is actually about that question". Same style of
# heuristic as diagnose_service's topic relevance check, reimplemented
# locally rather than imported, to keep this service independent.
EXISTING_ANSWER_OVERLAP_THRESHOLD = 0.12

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "best", "by", "do", "does",
    "for", "how", "in", "is", "it", "of", "on", "or", "that", "the", "to",
    "top", "was", "what", "who", "with", "worth", "you", "your", "2024", "2025",
}

_CATEGORY_AND_QUESTION_SYSTEM_PROMPT = f"""
You are a B2B market research analyst. You will be given the visible text
of a company's homepage. Do two things:

1. Identify the specific, narrow customer-facing category or niche this
company competes in. Describe it the way an independent industry analyst
or a customer researching solutions would describe it, NOT using the
company's own marketing language, and NOT using the company's name.

2. Generate exactly {REQUIRED_QUESTION_COUNT} realistic questions a
customer in this category would type into an AI assistant BEFORE knowing
about any specific vendor. Each question must describe a problem, task,
or need in the category. They must NOT mention this company's name, its
domain, or any of its specific product names.

Respond with ONLY a JSON object, no other text, in this exact shape:
{{"category": "<short customer-facing category description>", "questions": ["<question 1>", "..."]}}

{PLAIN_LANGUAGE_FORMAT_RULES}
""".strip()

_ANSWER_CAPSULE_SYSTEM_PROMPT = """
ROLE
You are rewriting real content from a company's own website into a short, self-contained paragraph that directly and plainly answers one specific question, in plain business English a non-technical person can follow.

STRICT RULES, NEVER VIOLATE
1. Only reuse and rephrase facts that are explicitly present in the page content you are given. Never add a fact, number, claim, or example that is not in that text.
2. If the given page content does not actually contain enough information to answer the question, say so by setting "answerable" to false rather than inventing an answer.
3. Never use parentheses of any kind.
4. Never use em dashes or double hyphens.
5. Never name any AI model, vendor, or technology such as DeepSeek, Perplexity, ChatGPT, Claude, GPT, LLM, or RAG.
6. Never use industry jargon or internal terminology beyond what the page itself uses in plain form.
7. Write like a person explaining this to a friend, not like marketing copy or a press release.
8. Always say "customer" or "customers", never "buyer" or "buyers".

STRUCTURE
Respond with ONLY a JSON object, no other text, no markdown formatting, no code fences, in this exact shape:
{"capsule_text": "<2 to 4 sentences that would make sense to someone reading them with zero other context, fully answering the question on their own>", "answerable": true or false}

If the page content does not support a real answer, set "answerable" to false and "capsule_text" to an empty string.
""".strip()


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9']+", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _jaccard_overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _find_existing_answer(question: str, page_text: str) -> str | None:
    """
    Look for a chunk of real page text that already reads as a
    self-contained answer to `question`: built from 1-3 consecutive
    sentences (fetch_page_text collapses real paragraph breaks, so
    sentences are the smallest structural unit available), long enough
    to stand alone, and with enough real word overlap with the question
    to plausibly be about it. Returns the actual matching text, or None.
    """
    sentences = _split_sentences(page_text)
    if not sentences:
        return None

    question_tokens = _tokenize(question)
    best_chunk, best_score = None, 0.0

    for window_size in (1, 2, 3):
        for start in range(len(sentences) - window_size + 1):
            chunk = " ".join(sentences[start : start + window_size])
            if len(chunk.split()) < MIN_SELF_CONTAINED_WORDS:
                continue
            score = _jaccard_overlap(question_tokens, _tokenize(chunk))
            if score > best_score:
                best_score, best_chunk = score, chunk

    if best_chunk and best_score >= EXISTING_ANSWER_OVERLAP_THRESHOLD:
        return best_chunk
    return None


def _call_deepseek_for_category_and_questions(homepage_text: str) -> dict:
    url = f"{settings.deepseek_api_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": _CATEGORY_AND_QUESTION_SYSTEM_PROMPT},
            {"role": "user", "content": homepage_text[:8000]},
        ],
        "temperature": 0,
    }
    response = requests.post(url, headers=headers, json=payload, timeout=settings.request_timeout_seconds)
    response.raise_for_status()
    raw_response = response.json()

    content = raw_response["choices"][0]["message"]["content"]
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    parsed = json.loads(cleaned)

    if "category" not in parsed or "questions" not in parsed:
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")

    return parsed


def _generate_brand_free_questions(company_name: str, domain: str, homepage_text: str) -> list[str] | None:
    """
    Repeatedly ask DeepSeek for brand-free customer questions until a
    batch of exactly REQUIRED_QUESTION_COUNT comes back with no mention
    of the brand name or domain. A contaminated or wrong-sized batch is
    discarded entirely, never trimmed or padded. Returns None if no
    clean batch was produced within MAX_GENERATION_ATTEMPTS.
    """
    company_lower = company_name.lower()
    domain_lower = domain.lower()

    for _ in range(MAX_GENERATION_ATTEMPTS):
        result = _call_deepseek_for_category_and_questions(homepage_text)
        raw_questions = [q.strip() for q in result.get("questions", []) if isinstance(q, str) and q.strip()]

        if len(raw_questions) != REQUIRED_QUESTION_COUNT:
            continue

        contains_brand = any(
            company_lower in q.lower() or (domain_lower and domain_lower in q.lower()) for q in raw_questions
        )
        if contains_brand:
            continue

        return [sanitize_banned_formatting(q) if find_banned_formatting(q) else q for q in raw_questions]

    return None


def _suggest_placement(question: str) -> str:
    """Small deterministic hint for where a capsule would make sense, based on real keywords in the question."""
    question_lower = question.lower()
    if any(word in question_lower for word in ("price", "pricing", "cost", "plan")):
        return "Add near the top of the pricing page, or create one if the site doesn't have one."
    if any(word in question_lower for word in ("secure", "security", "compliance", "privacy")):
        return "Add to a security or trust page, or create a short trust section if none exists."
    if any(word in question_lower for word in ("integrate", "integration", "api", "connect")):
        return "Add to a product or integrations page."
    return "Add to a visible FAQ section on the homepage or a dedicated FAQ page."


def _call_deepseek_for_capsule(question: str, page_text: str) -> dict:
    url = f"{settings.deepseek_api_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    user_content = f"Question: {question}\n\nReal page content:\n{page_text[:6000]}"
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": _ANSWER_CAPSULE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
    }
    response = requests.post(url, headers=headers, json=payload, timeout=settings.request_timeout_seconds)
    response.raise_for_status()
    raw_response = response.json()

    content = raw_response["choices"][0]["message"]["content"]
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    parsed = json.loads(cleaned)

    if "capsule_text" not in parsed:
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")

    return parsed


def _build_capsule_for_question(question: str, homepage_text: str) -> dict:
    """
    Build one capsule. Never raises: any failure in the LLM path is
    caught and reported as generation_failed on this one capsule, so one
    bad question can never take down the whole batch (see
    generate_answer_capsules).
    """
    existing = _find_existing_answer(question, homepage_text)
    if existing:
        return {
            "question": question,
            "existing_answer_found": True,
            "capsule_text": None,
            "placement_hint": "Already answered on the site; no new capsule needed.",
            "generation_failed": False,
        }

    if not settings.has_deepseek_key():
        return {
            "question": question,
            "existing_answer_found": False,
            "capsule_text": None,
            "placement_hint": None,
            "generation_failed": True,
            "reason": "DEEPSEEK_API_KEY is not configured.",
        }

    try:
        parsed = _call_deepseek_for_capsule(question, homepage_text)
    except (requests.RequestException, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        return {
            "question": question,
            "existing_answer_found": False,
            "capsule_text": None,
            "placement_hint": None,
            "generation_failed": True,
            "reason": f"Capsule generation failed: {exc}",
        }

    if not parsed.get("answerable", True) or not parsed.get("capsule_text"):
        return {
            "question": question,
            "existing_answer_found": False,
            "capsule_text": None,
            "placement_hint": None,
            "generation_failed": False,
            "reason": "The page's real content does not contain enough information to answer this question.",
        }

    capsule_text = parsed["capsule_text"]
    if find_banned_formatting(capsule_text):
        capsule_text = sanitize_banned_formatting(capsule_text)

    return {
        "question": question,
        "existing_answer_found": False,
        "capsule_text": capsule_text,
        "placement_hint": _suggest_placement(question),
        "generation_failed": False,
    }


def generate_answer_capsules(domain: str, company_name: str, questions: list[str] | None = None) -> dict:
    """
    Top-level entry point. If `questions` isn't supplied (e.g. by a
    caller that already has them from a diagnosis run), a fresh
    brand-free set is generated here. Each question is handled
    independently: a failure on one never blocks the others, it is only
    marked generation_failed on that entry.
    """
    try:
        homepage_text = fetch_page_text(domain)
    except requests.RequestException as exc:
        return {"status": "insufficient_data", "reason": f"Could not open {domain}: {exc}", "capsules": []}

    if not homepage_text:
        return {
            "status": "insufficient_data",
            "reason": f"No readable text found on {domain}.",
            "capsules": [],
        }

    if not questions:
        if not settings.has_deepseek_key():
            return {
                "status": "insufficient_data",
                "reason": "DEEPSEEK_API_KEY is not configured; cannot generate customer questions.",
                "capsules": [],
            }
        try:
            questions = _generate_brand_free_questions(company_name, domain, homepage_text)
        except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as exc:
            return {
                "status": "insufficient_data",
                "reason": f"Could not generate customer questions: {exc}",
                "capsules": [],
            }
        if not questions:
            return {
                "status": "insufficient_data",
                "reason": f"Could not generate brand-free customer questions after {MAX_GENERATION_ATTEMPTS} attempts.",
                "capsules": [],
            }

    capsules = [_build_capsule_for_question(question, homepage_text) for question in questions]

    return {"status": "ok", "capsules": capsules}
