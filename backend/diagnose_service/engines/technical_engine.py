"""
Technical readability engine.

Checks whether AI crawlers can actually reach and understand this
domain's homepage, using only deterministic, rule-based signals read
directly from the page's own robots.txt and raw HTML. No LLM call
anywhere in this module, so the same input always produces the same
score.

Three factors, each worth a fixed share of the 100-point score:
1. AI crawler access (40 points): whether robots.txt allows GPTBot,
   PerplexityBot, ClaudeBot, and Google-Extended, scored by the
   fraction of those 4 that are actually allowed.
2. Structured data (30 points): whether the page has a parseable
   <script type="application/ld+json"> block with a recognized @type
   (Organization, Product, SoftwareApplication, FAQPage, WebSite)
   anywhere in it, including nested inside an @graph array — the
   common schema.org pattern of bundling several entities under one
   top-level @graph list.
3. Heading structure (30 points): at least one <h1> earns 15 points,
   at least two <h2> elements earns the other 15, independently.

Check weights are our own judgment. The three factors themselves come
from GEO-16 (Kumar and Palkhouski, 2025).
"""

import json

import requests
from bs4 import BeautifulSoup
from urllib.robotparser import RobotFileParser

from shared.config import settings
from shared._fetch_utils import fetch_page_html
from engines._plain_finding_generator import make_plain_finding
from models import DimensionScore

DIMENSION_NAME = "technical_readability"

# The same 4 AI crawler user agents agent_service's own
# technical_package_engine.py checks — duplicated here rather than
# imported, since diagnose_service and agent_service are kept
# independent services.
AI_CRAWLER_USER_AGENTS = ["GPTBot", "PerplexityBot", "ClaudeBot", "Google-Extended"]
CRAWLER_ACCESS_POINTS = 40

RECOGNIZED_JSON_LD_TYPES = {"Organization", "Product", "SoftwareApplication", "FAQPage", "WebSite"}
STRUCTURED_DATA_POINTS = 30

HEADING_POINTS_H1 = 15
HEADING_POINTS_H2 = 15
MIN_H1_COUNT = 1
MIN_H2_COUNT = 2

METHOD_NOTE = "Check weights are our own judgment. The three factors come from GEO-16 (Kumar and Palkhouski, 2025)."


def _check_crawler_access(domain: str) -> dict:
    """
    Fetch robots.txt for `domain` and check whether each of
    AI_CRAWLER_USER_AGENTS can fetch the homepage, via
    urllib.robotparser (standard-library robots.txt semantics, not a
    hand-rolled parser). No robots.txt at all (a 404, or the request
    itself failing) is treated as every crawler being allowed —
    robots.txt's own spec says no file means no restriction.
    """
    full_url = domain if domain.startswith("http") else f"https://{domain}"
    robots_url = full_url.rstrip("/") + "/robots.txt"

    try:
        headers = {"User-Agent": settings.http_user_agent}
        response = requests.get(robots_url, headers=headers, timeout=settings.request_timeout_seconds)
        if response.status_code == 404:
            existing_content = ""
        else:
            response.raise_for_status()
            existing_content = response.text
    except requests.RequestException:
        existing_content = ""

    parser = RobotFileParser()
    parser.parse(existing_content.splitlines())

    allowed, blocked = [], []
    for agent in AI_CRAWLER_USER_AGENTS:
        (allowed if parser.can_fetch(agent, full_url + "/") else blocked).append(agent)

    points = round(CRAWLER_ACCESS_POINTS * len(allowed) / len(AI_CRAWLER_USER_AGENTS))
    return {
        "points": points,
        "max_points": CRAWLER_ACCESS_POINTS,
        "allowed_crawlers": allowed,
        "blocked_crawlers": blocked,
    }


def _find_json_ld_types(node) -> list[str]:
    """
    Recursively collect every @type found anywhere in a parsed JSON-LD
    structure: at the top level, nested inside an @graph array (the
    common schema.org pattern of bundling several entities under one
    top-level @graph list), or nested any other way. @type itself may
    be a single string or a list of strings; both forms are handled.
    """
    found = []
    if isinstance(node, dict):
        node_type = node.get("@type")
        if node_type is not None:
            candidates = node_type if isinstance(node_type, list) else [node_type]
            found.extend(candidate for candidate in candidates if isinstance(candidate, str))
        for value in node.values():
            found.extend(_find_json_ld_types(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_find_json_ld_types(item))
    return found


def _check_structured_data(soup: BeautifulSoup) -> dict:
    """
    Look at every <script type="application/ld+json"> block on the
    page and recursively collect every @type found anywhere inside it.
    A block that isn't valid JSON is skipped rather than failing the
    whole check. `found_types` lists every distinct @type actually
    found, recognized or not; `points` is full marks as soon as at
    least one of them is a recognized type.
    """
    found_types = []
    for script_tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not script_tag.string:
            continue
        try:
            parsed = json.loads(script_tag.string)
        except json.JSONDecodeError:
            continue
        found_types.extend(_find_json_ld_types(parsed))

    unique_types = sorted(set(found_types))
    has_recognized_type = any(found_type in RECOGNIZED_JSON_LD_TYPES for found_type in unique_types)

    return {
        "points": STRUCTURED_DATA_POINTS if has_recognized_type else 0,
        "max_points": STRUCTURED_DATA_POINTS,
        "found_types": unique_types,
    }


def _check_heading_structure(soup: BeautifulSoup) -> dict:
    """At least MIN_H1_COUNT <h1> earns its points; at least MIN_H2_COUNT <h2> elements earns the rest, independently."""
    h1_count = len(soup.find_all("h1"))
    h2_count = len(soup.find_all("h2"))

    h1_points = HEADING_POINTS_H1 if h1_count >= MIN_H1_COUNT else 0
    h2_points = HEADING_POINTS_H2 if h2_count >= MIN_H2_COUNT else 0

    return {
        "points": h1_points + h2_points,
        "max_points": HEADING_POINTS_H1 + HEADING_POINTS_H2,
        "h1_count": h1_count,
        "h2_count": h2_count,
    }


def check_technical_readability(domain: str) -> DimensionScore:
    """
    Score how reachable and parseable `domain`'s homepage is for AI
    crawlers, from 3 deterministic checks: robots.txt access,
    structured data, and heading structure.

    Returns score == -1 ("insufficient data") only when the homepage
    itself cannot be fetched at all. The robots.txt check always runs
    as part of the normal "ok" path, since it is a separate request
    that doesn't depend on homepage content.
    """
    try:
        html = fetch_page_html(domain)
    except requests.RequestException as exc:
        fallback = (
            f"We couldn't open the homepage at {domain} to check whether AI crawlers can read it, so "
            "there's nothing real to report here. Today, try loading the homepage yourself to confirm "
            "it's reachable."
        )
        facts_summary = (
            "Dimension: whether AI crawlers can reach and understand this company's homepage.\n"
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

    soup = BeautifulSoup(html, "html.parser")

    crawler_access = _check_crawler_access(domain)
    structured_data = _check_structured_data(soup)
    heading_structure = _check_heading_structure(soup)

    score = crawler_access["points"] + structured_data["points"] + heading_structure["points"]

    fallback = _build_fallback_plain_finding(crawler_access, structured_data, heading_structure)
    facts_summary = _build_facts_summary(crawler_access, structured_data, heading_structure)
    plain_finding, generation_log = make_plain_finding(facts_summary, fallback)

    return DimensionScore(
        dimension=DIMENSION_NAME,
        score=score,
        details={
            "status": "ok",
            "method_note": METHOD_NOTE,
            "crawler_access": crawler_access,
            "structured_data": structured_data,
            "heading_structure": heading_structure,
            "generation_log": generation_log,
            "plain_finding": plain_finding,
        },
    )


def _build_facts_summary(crawler_access: dict, structured_data: dict, heading_structure: dict) -> str:
    """Plain-text real facts handed to the AI writer — see engines._plain_finding_generator."""
    total_crawlers = len(crawler_access["allowed_crawlers"]) + len(crawler_access["blocked_crawlers"])
    lines = [
        "Dimension: whether AI crawlers can reach and understand this company's homepage.",
        f"AI crawler access: {len(crawler_access['allowed_crawlers'])} out of {total_crawlers} AI "
        "crawlers are allowed by robots.txt.",
    ]
    if crawler_access["blocked_crawlers"]:
        lines.append(f"Blocked crawlers: {', '.join(crawler_access['blocked_crawlers'])}.")
    if structured_data["found_types"]:
        lines.append(f"Structured data found on the page: {', '.join(structured_data['found_types'])}.")
    else:
        lines.append("No recognized structured data was found on the page.")
    lines.append(
        f"Heading structure: {heading_structure['h1_count']} h1 heading(s) and "
        f"{heading_structure['h2_count']} h2 heading(s)."
    )
    return "\n".join(lines)


def _build_fallback_plain_finding(crawler_access: dict, structured_data: dict, heading_structure: dict) -> str:
    """Founder-readable finding built only from this run's real checks, used if AI writing fails."""
    issues = []
    if crawler_access["blocked_crawlers"]:
        issues.append(f"robots.txt blocks {', '.join(crawler_access['blocked_crawlers'])} from reading this site")
    if not structured_data["found_types"]:
        issues.append("the page has no structured data telling AI tools what kind of page it is")
    if heading_structure["h1_count"] < MIN_H1_COUNT:
        issues.append("the page has no h1 heading at all")
    if heading_structure["h2_count"] < MIN_H2_COUNT:
        issues.append(f"the page has only {heading_structure['h2_count']} h2 headings")

    if not issues:
        return (
            "AI crawlers can fully reach and understand this homepage. Nothing in robots.txt blocks "
            "them, the page has structured data describing what it is, and the heading structure is "
            "clean. Today, just keep this in place the next time the page gets redesigned."
        )

    issue_text = "; ".join(issues)
    return (
        f"Right now, {issue_text}. Any of these can make it harder for AI crawlers to find and "
        "correctly understand this page, even if a human visitor sees nothing wrong with it. Today, "
        "fix the first issue on that list, since it is the most direct block on AI crawlers actually "
        "reading the page at all."
    )
