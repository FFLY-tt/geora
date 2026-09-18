"""
Fact consistency engine.

Compares specific, checkable facts (pricing, supported regions, core
feature claims) between the target company's own homepage and what G2 or
Capterra are reported to say about it.

Design note: G2 and Capterra both have strong anti-bot protection, so
directly scraping them is unreliable and would require bypassing that
protection, which this project does not do. Instead, this engine asks
Perplexity (which already indexes and cites those sites) what it knows
about the company from G2/Capterra, and treats Perplexity's cited answer
as the third-party source to compare against the official homepage.
"""

import json
import re

import requests

from shared.config import settings
from shared._fetch_utils import fetch_page_text
from engines._plain_finding_generator import make_plain_finding
from shared._text_format import PLAIN_LANGUAGE_FORMAT_RULES, find_banned_formatting, sanitize_banned_formatting
from models import DimensionScore

DIMENSION_NAME = "fact_consistency"

MAX_FORMAT_ATTEMPTS = 2

_FACT_COMPARISON_SYSTEM_PROMPT = f"""
You are a strict fact-checking auditor. You will be given two pieces of
text: TEXT_A, the company's own homepage and possibly its pricing page,
and TEXT_B, an AI search answer summarizing what G2 and Capterra say
about the company, with citations. Extract and compare these categories
of checkable facts, where present: pricing, supported regions or
countries, and core feature claims.

For each category, decide whether TEXT_A and TEXT_B agree, disagree, or
whether the category is not mentioned in one or both texts. If it is not
mentioned in both, it cannot be compared and must be excluded from both
lists below.

Respond with ONLY a JSON object, no other text, in this exact shape:
{{"compared_fields": ["<category names that were present and comparable in both texts>"], "conflicts": [{{"field": "<category name>", "official_says": "<what TEXT_A says>", "third_party_says": "<what TEXT_B says>"}}]}}

Only include a field in "conflicts" if both texts actually mention it AND
the values genuinely disagree. Never invent a value that is not present in
the text you were given.

{PLAIN_LANGUAGE_FORMAT_RULES}
""".strip()


def _build_g2_capterra_query(company_name: str) -> str:
    """Build the Perplexity query used to look up G2/Capterra data."""
    return (
        f"According to G2.com or Capterra.com, what is {company_name}'s "
        f"pricing, supported regions, and core features?"
    )


def _call_perplexity(query: str) -> dict:
    """
    Send `query` to the Perplexity chat completions API and return the
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


def _find_g2_or_capterra_citations(citations: list) -> list:
    """Return the subset of `citations` whose URL contains g2.com or capterra.com."""
    return [c for c in citations if "g2.com" in c.lower() or "capterra.com" in c.lower()]


def _fetch_official_text(domain: str) -> tuple[str, list[str]]:
    """
    Fetch the domain's homepage text, plus a best-effort guess at its
    pricing page. The homepage fetch failing is a hard failure (raised to
    the caller); the pricing-page guess failing is not, since it was only
    a guess, not a known real page.

    Returns (combined_text, fetched_urls). Raises requests.RequestException
    if the homepage itself could not be fetched.
    """
    homepage_url = domain if domain.startswith("http") else f"https://{domain}"
    homepage_text = fetch_page_text(domain)
    fetched_urls = [homepage_url]

    pricing_url = f"{homepage_url.rstrip('/')}/pricing"
    try:
        pricing_text = fetch_page_text(pricing_url)
        fetched_urls.append(pricing_url)
    except requests.RequestException:
        pricing_text = ""

    combined_text = homepage_text
    if pricing_text:
        combined_text = f"{homepage_text}\n\n--- Pricing page ---\n\n{pricing_text}"

    return combined_text, fetched_urls


def _call_deepseek_for_fact_comparison(company_name: str, official_text: str, third_party_text: str) -> dict:
    """
    Send both texts to DeepSeek and return the parsed JSON comparison.
    Raises requests.RequestException on network/HTTP failure, and
    ValueError if the model response isn't valid JSON in the expected shape.
    """
    url = f"{settings.deepseek_api_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    # Truncate each side to keep the request reasonably sized.
    user_content = (
        f"The company being checked is: {company_name}\n\n"
        f"TEXT_A (official homepage / pricing page):\n{official_text[:6000]}\n\n"
        f"TEXT_B (AI search answer citing G2/Capterra):\n{third_party_text[:6000]}"
    )
    # Low temperature for consistent, repeatable diagnostic results across runs.
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": _FACT_COMPARISON_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
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

    if "compared_fields" not in parsed or "conflicts" not in parsed:
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")

    return parsed


def _comparison_text_fields(comparison: dict) -> list[str]:
    """All the free-text strings in a comparison result that could end up quoted in plain_finding."""
    fields = list(comparison.get("compared_fields", []))
    for entry in comparison.get("conflicts", []):
        fields.extend([entry.get("field", ""), entry.get("official_says", ""), entry.get("third_party_says", "")])
    return fields


def _get_clean_fact_comparison(company_name: str, official_text: str, third_party_text: str) -> tuple[dict, list]:
    """
    Call DeepSeek for the fact comparison, retrying once if any of the
    free-text fields (category names, official_says, third_party_says)
    come back with banned formatting instead of the plain, direct
    language the prompt asks for. If still not clean after
    MAX_FORMAT_ATTEMPTS, every affected field is deterministically
    sanitized rather than shown to a user as-is.

    Returns (comparison, format_check_log): the log records what was
    found on each attempt, kept for debugging.
    """
    format_check_log = []
    comparison = None

    for attempt_number in range(1, MAX_FORMAT_ATTEMPTS + 1):
        comparison = _call_deepseek_for_fact_comparison(company_name, official_text, third_party_text)
        issues = set()
        for field_text in _comparison_text_fields(comparison):
            issues.update(find_banned_formatting(field_text))
        if not issues:
            format_check_log.append({"attempt": attempt_number, "issues": []})
            return comparison, format_check_log
        format_check_log.append({"attempt": attempt_number, "issues": sorted(issues)})

    comparison["compared_fields"] = [sanitize_banned_formatting(f) for f in comparison["compared_fields"]]
    for entry in comparison["conflicts"]:
        entry["field"] = sanitize_banned_formatting(entry.get("field", ""))
        entry["official_says"] = sanitize_banned_formatting(entry.get("official_says", ""))
        entry["third_party_says"] = sanitize_banned_formatting(entry.get("third_party_says", ""))
    format_check_log.append({"attempt": "sanitized", "issues": []})
    return comparison, format_check_log


def _score_from_conflict_count(conflict_count: int) -> int:
    """
    Map the number of conflicting fields to a 0-100 score. No conflicts
    scores high; each additional conflict lowers the score further.
    """
    if conflict_count == 0:
        return 95
    if conflict_count == 1:
        return 60
    if conflict_count == 2:
        return 35
    return 10


def check_fact_consistency(company_name: str, domain: str) -> DimensionScore:
    """
    Compare checkable facts between `domain`'s official homepage and what
    Perplexity reports G2/Capterra say about `company_name`.

    Returns score == -1 ("insufficient data") when Perplexity is not
    configured or its request fails, when it finds no G2/Capterra
    citations for this company, when the official homepage can't be
    fetched, when nothing comparable was found, or when DeepSeek is not
    configured / fails to produce a valid comparison. Never fabricates a
    conflict, or a "no conflicts found" result, without real data backing it.
    """
    if not settings.has_perplexity_key():
        fallback = (
            "We couldn't check whether independent listings agree with the official site, because "
            "the connection to the search tool that looks up third-party sources isn't turned on "
            "yet. Today, turn on that connection so this check can run for real."
        )
        facts_summary = (
            "Dimension: whether facts on the company's own website match what independent listings "
            "such as review sites say about it.\n"
            "Status: We could not run this check because a required connection is not turned on."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": "PERPLEXITY_API_KEY is not configured; no G2/Capterra search was performed.",
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    query = _build_g2_capterra_query(company_name)

    try:
        raw_response = _call_perplexity(query)
    except requests.RequestException:
        fallback = (
            "We tried looking up what independent review sites say about this brand, but the "
            "request failed this time, so we don't have a real comparison to report. This is "
            "usually temporary. Today, just try running the report again."
        )
        facts_summary = (
            "Dimension: whether facts on the company's own website match what independent listings "
            "such as review sites say about it.\n"
            "Status: A request to an outside search tool failed this time."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": "Perplexity request failed",
                "query": query,
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    choices = raw_response.get("choices", [])
    answer_text = ""
    if choices:
        answer_text = choices[0].get("message", {}).get("content", "") or ""
    citations = raw_response.get("citations", []) or []

    g2_capterra_citations = _find_g2_or_capterra_citations(citations)

    if not g2_capterra_citations:
        fallback = (
            f'We looked for what G2 or Capterra say about "{company_name}" specifically, and found '
            "no listing for it on either site. There's nothing independent to compare the official "
            "site against yet. That's worth knowing on its own. Customers who check review sites "
            "before buying won't find this brand there at all. Today, claim or create a listing on "
            "G2 or Capterra. It's usually free and only takes a few minutes."
        )
        facts_summary = (
            "Dimension: whether facts on the company's own website match what independent listings "
            "such as review sites say about it.\n"
            f'Status: No listing for "{company_name}" was found on either of the two review sites we '
            "checked."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": "No G2 or Capterra data found for this company via Perplexity search",
                "query": query,
                "citations": citations,
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    if not answer_text.strip():
        fallback = (
            "We found references to G2/Capterra listings for this brand, but didn't get any real "
            "summary of what they say, so there's nothing to compare yet. Today, just try running "
            "the report again."
        )
        facts_summary = (
            "Dimension: whether facts on the company's own website match what independent listings "
            "such as review sites say about it.\n"
            "Status: We found review site listings for this company but got no usable summary of "
            "what they say."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": "Perplexity cited G2/Capterra sources but returned no answer text to compare.",
                "query": query,
                "citations": citations,
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    try:
        official_text, official_urls_fetched = _fetch_official_text(domain)
    except requests.RequestException:
        fallback = (
            "We had independent listing data ready to compare, but couldn't open the official "
            "homepage to compare it against, so this check can't finish. Today, try loading the "
            "homepage yourself to confirm it's reachable."
        )
        facts_summary = (
            "Dimension: whether facts on the company's own website match what independent listings "
            "such as review sites say about it.\n"
            f"Status: We had independent listing data ready, but could not open the official homepage "
            f"at {domain} to compare it against."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": f"Failed to fetch domain homepage '{domain}'",
                "query": query,
                "citations": citations,
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    if not official_text:
        fallback = (
            "We had independent listing data ready to compare, but found no real readable text on "
            "the official homepage to compare it against. Today, check that your homepage's core "
            "copy shows up right away for a visitor."
        )
        facts_summary = (
            "Dimension: whether facts on the company's own website match what independent listings "
            "such as review sites say about it.\n"
            "Status: We had independent listing data ready, but the official homepage had no "
            "readable text to compare it against."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": f"Homepage for '{domain}' returned no extractable body text (possibly JS-rendered).",
                "query": query,
                "citations": citations,
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
                "reason": "DEEPSEEK_API_KEY is not configured; sources were gathered but not compared.",
                "query": query,
                "citations": citations,
                "official_urls_fetched": official_urls_fetched,
                "plain_finding": (
                    "We gathered both the official homepage and what independent listings say, but the "
                    "tool that compares the two for mismatches isn't turned on yet, so we don't have a "
                    "real verdict. Today, turn on that connection so this comparison can run for real."
                ),
            },
        )

    try:
        comparison, format_check_log = _get_clean_fact_comparison(company_name, official_text, answer_text)
    except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as exc:
        fallback = (
            "We gathered both sources but hit a technical hiccup while comparing them, so there's "
            "no real verdict this run. Today, just try running the report again."
        )
        facts_summary = (
            "Dimension: whether facts on the company's own website match what independent listings "
            "such as review sites say about it.\n"
            "Status: We gathered both sources but a temporary technical problem stopped the "
            "comparison from finishing."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": f"DeepSeek comparison failed: {exc}",
                "query": query,
                "citations": citations,
                "official_urls_fetched": official_urls_fetched,
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    compared_fields = comparison["compared_fields"]
    conflicts = comparison["conflicts"]

    if not compared_fields:
        # Nothing was actually comparable between the two sources (e.g.
        # neither mentions pricing, regions, or features) — there is no
        # real basis to claim "no conflicts found".
        fallback = (
            "We compared the official site against what independent listings say about "
            f'"{company_name}", but neither side actually mentioned enough specifics, like '
            "pricing, coverage, or concrete features, to compare anything meaningfully. That's "
            "its own finding. If independent listings don't say much concrete about this brand, "
            "customers researching it there won't learn much either. Today, make sure the "
            "G2 or Capterra listing and the homepage both spell out specifics like pricing and "
            "key features, not just a general description."
        )
        facts_summary = (
            "Dimension: whether facts on the company's own website match what independent listings "
            "such as review sites say about it.\n"
            f'Status: We compared the official site against independent listings for "{company_name}", '
            "but neither side mentioned enough specifics such as pricing, coverage, or features to "
            "compare anything meaningfully."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": "No comparable fields (pricing, supported regions, core features) were found in both sources.",
                "query": query,
                "citations": citations,
                "official_urls_fetched": official_urls_fetched,
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    score = _score_from_conflict_count(len(conflicts))

    fallback = _build_fallback_plain_finding(company_name, compared_fields, conflicts)
    facts_summary = _build_facts_summary(company_name, compared_fields, conflicts)
    plain_finding, generation_log = make_plain_finding(facts_summary, fallback)

    return DimensionScore(
        dimension=DIMENSION_NAME,
        score=score,
        details={
            "status": "ok",
            "query": query,
            "citations": citations,
            "official_urls_fetched": official_urls_fetched,
            "compared_fields": compared_fields,
            "conflicts": conflicts,
            "format_check": format_check_log,
            "generation_log": generation_log,
            "plain_finding": plain_finding,
        },
    )


def _build_facts_summary(company_name: str, compared_fields: list, conflicts: list) -> str:
    """Plain-text real facts handed to the AI writer — see engines._plain_finding_generator."""
    fields_note = ", ".join(compared_fields)

    if not conflicts:
        return (
            "Dimension: whether facts on the company's own website match what independent listings "
            "such as review sites say about it.\n"
            f'We compared what the official site says about {fields_note} against what independent '
            f'listings say about "{company_name}". They agree on all of it. No disagreement was found.'
        )

    conflict_lines = [
        f'On {entry["field"]}, the official site says: {entry["official_says"]} Independent listings '
        f'say: {entry["third_party_says"]}'
        for entry in conflicts
    ]
    conflict_summary = " ".join(conflict_lines)
    return (
        "Dimension: whether facts on the company's own website match what independent listings such "
        "as review sites say about it.\n"
        f"We compared the official site against independent listings for {fields_note} and found a "
        f"real mismatch. {conflict_summary}"
    )


def _build_fallback_plain_finding(company_name: str, compared_fields: list, conflicts: list) -> str:
    """Founder-readable finding built only from this run's real compared fields and conflicts, used if AI writing fails."""
    fields_note = ", ".join(compared_fields)

    if not conflicts:
        return (
            f"We compared what the official site says about {fields_note} against what independent "
            f'listings say about "{company_name}". We found no disagreement. Both sides tell the same '
            "story. That's good. Customers who cross-check independent sources before buying won't find "
            "anything that makes them second-guess the official claims. Today, no action needed here. "
            "Just make sure future pricing or feature changes get updated on both the official site and "
            "any listings at the same time, so they stay in sync."
        )

    conflict_lines = [
        f'on {entry["field"]}, the official site says "{entry["official_says"]}" while independent '
        f'listings say "{entry["third_party_says"]}"'
        for entry in conflicts
    ]
    conflict_summary = "; ".join(conflict_lines)
    return (
        f"We compared the official site against independent listings for {fields_note}, and found a real "
        f"mismatch: {conflict_summary}. That's a problem. A customer who checks both places before buying "
        "will hit conflicting information right when they're deciding whether to trust this brand. That "
        "hesitation often costs the deal. Today, pick the first mismatch above and update whichever side "
        "is out of date."
    )
