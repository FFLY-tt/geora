"""
Evidence density engine.

Fetches the target domain's homepage, extracts the visible body text, and
asks a DeepSeek chat model to judge whether the copy contains concrete
evidence (numbers, citations, specific feature descriptions) or is mostly
vague marketing language.

Page fetching itself is delegated to engines._fetch_utils.fetch_page_text
(shared with fact_engine.py) — see that module's docstring for the note
about anti-bot limitations and the Jina AI Reader API as a future upgrade.
"""

import json
import re

import requests

from config import settings
from engines._fetch_utils import fetch_page_text
from engines._plain_finding_generator import make_plain_finding
from engines._text_format import PLAIN_LANGUAGE_FORMAT_RULES, find_banned_formatting, sanitize_banned_formatting
from models import DimensionScore

DIMENSION_NAME = "evidence_density"

MAX_FORMAT_ATTEMPTS = 2

_EVIDENCE_JUDGE_SYSTEM_PROMPT = f"""
You are a strict content auditor working on behalf of an outside analyst.
You will be given the visible text of a THIRD PARTY company's homepage.
That company is not you, and this task is not about you: you are reading
their page on their behalf, the same way an auditor reviews a client's
document. Never describe yourself, your own identity, what kind of
system you are, or how you work. Only describe what is written on the
page you were given.

Judge whether that page's content is backed by concrete evidence, such as
specific statistics, named case studies, cited sources, precise feature
or spec descriptions, versus being mostly vague marketing adjectives like
"best-in-class", "revolutionary", or "world-leading" with no substance.

Respond with ONLY a JSON object, no other text, in this exact shape:
{{"score": <integer 0-100>, "reasoning": "<2-4 sentences explaining the score, quoting concrete examples from the page's own text if any exist>"}}

A score of 0 means pure marketing fluff with zero concrete evidence.
A score of 100 means the page is dense with specific, verifiable facts.

{PLAIN_LANGUAGE_FORMAT_RULES}
""".strip()


def _call_deepseek_for_evidence_judgment(page_text: str) -> dict:
    """
    Send the page text to DeepSeek and return the parsed JSON judgment.
    Raises requests.RequestException on network/HTTP failure, and
    ValueError if the model response isn't valid JSON in the expected shape.
    """
    url = f"{settings.deepseek_api_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    # Truncate to keep the request reasonably sized; homepages rarely need
    # more than this to judge overall tone and evidence density.
    truncated_text = page_text[:8000]
    # Low temperature for consistent, repeatable diagnostic results across runs.
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": _EVIDENCE_JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": truncated_text},
        ],
        "temperature": 0.2,
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

    if "score" not in parsed or "reasoning" not in parsed:
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")

    return parsed


def _get_clean_evidence_judgment(page_text: str) -> tuple[dict, list]:
    """
    Call DeepSeek for the evidence judgment, retrying once if the
    "reasoning" text comes back with banned formatting (em dash, double
    hyphen, parentheses, or brackets) instead of the plain, direct
    language the prompt asks for. If it is still not clean after
    MAX_FORMAT_ATTEMPTS, the reasoning text is deterministically
    sanitized rather than shown to a user as-is.

    Returns (judgment, format_check_log): the log records what was found
    on each attempt, kept for debugging.
    """
    format_check_log = []
    judgment = None

    for attempt_number in range(1, MAX_FORMAT_ATTEMPTS + 1):
        judgment = _call_deepseek_for_evidence_judgment(page_text)
        issues = find_banned_formatting(judgment["reasoning"])
        if not issues:
            format_check_log.append({"attempt": attempt_number, "issues": []})
            return judgment, format_check_log
        format_check_log.append({"attempt": attempt_number, "issues": issues})

    judgment["reasoning"] = sanitize_banned_formatting(judgment["reasoning"])
    format_check_log.append({"attempt": "sanitized", "issues": []})
    return judgment, format_check_log


def check_evidence_density(domain: str) -> DimensionScore:
    """
    Score how evidence-dense (vs. vague marketing) the domain's homepage
    copy is, as judged by a DeepSeek model.

    Returns score == -1 ("insufficient data") when the homepage could not
    be fetched, or when DeepSeek is not configured / fails to produce a
    valid judgment. Never fabricates a score in those cases.
    """
    try:
        page_text = fetch_page_text(domain)
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

    try:
        judgment, format_check_log = _get_clean_evidence_judgment(page_text)
    except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as exc:
        fallback = (
            "We fetched the homepage, but hit a technical hiccup while judging whether its content "
            "is backed by real substance, so there's no real verdict this run. This is usually "
            "temporary. Today, just try running the report again."
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
                "reason": f"DeepSeek evaluation failed: {exc}",
                "fetched_text_preview": page_text[:500],
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    score = int(judgment["score"])
    score = max(0, min(100, score))

    fallback = _build_fallback_plain_finding(score, judgment["reasoning"])
    facts_summary = _build_facts_summary(score, judgment["reasoning"])
    plain_finding, generation_log = make_plain_finding(facts_summary, fallback)

    return DimensionScore(
        dimension=DIMENSION_NAME,
        score=score,
        details={
            "status": "ok",
            "reasoning": judgment["reasoning"],
            "format_check": format_check_log,
            "fetched_text_preview": page_text[:500],
            "fetched_text_length": len(page_text),
            "generation_log": generation_log,
            "plain_finding": plain_finding,
        },
    )


def _build_facts_summary(score: int, reasoning: str) -> str:
    """Plain-text real facts handed to the AI writer — see engines._plain_finding_generator."""
    return (
        "Dimension: whether the company's homepage backs up its claims with real, specific evidence, "
        "such as numbers, named examples, or sources, instead of vague marketing language.\n"
        f"An analysis of the homepage content found: {reasoning}"
    )


def _build_fallback_plain_finding(score: int, reasoning: str) -> str:
    """Founder-readable finding built on top of the real reasoning for this run, used if AI writing fails."""
    if score >= 70:
        return (
            f"{reasoning} That's a good sign. Content backed by specifics like this is exactly what AI "
            "search tools and skeptical customers tend to trust and quote. Today, take that same specific, "
            "detail-backed style and apply it to one more page that's currently thinner on substance."
        )
    if score <= 30:
        return (
            f"{reasoning} In practice, that means the homepage leans on broad claims a customer can't "
            "verify, rather than specifics they can check for themselves. Both AI search tools and "
            "cautious customers tend to discount that kind of copy. Today, pick the single biggest claim "
            "on the homepage and add one concrete number, named example, or source next to it."
        )
    return (
        f"{reasoning} That's a mixed picture. Part of the page backs itself up with specifics, and part "
        "leans on broader claims that are harder for a customer, or an AI search tool, to verify. Today, "
        "find one broad claim on the homepage and swap it for a specific number or named example."
    )
