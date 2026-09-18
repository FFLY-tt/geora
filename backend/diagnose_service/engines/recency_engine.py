"""
Content recency engine.

Checks how recently the target domain's homepage was updated, using only
two real, verifiable signals:

1. The HTTP `Last-Modified` response header.
2. A `dateModified` field inside schema.org structured data (JSON-LD
   `<script type="application/ld+json">` blocks, or a
   `<meta itemprop="dateModified" content="...">` tag).

If neither signal is present, this engine reports "insufficient data"
instead of fabricating a score, since there is no real basis to judge
recency.
"""

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup

from shared.config import settings
from engines._plain_finding_generator import make_plain_finding
from models import DimensionScore

DIMENSION_NAME = "content_recency"


def _fetch_homepage(domain: str) -> requests.Response:
    """
    Fetch the homepage for `domain` and return the raw response (so both
    headers and body are available to the caller). Raises
    requests.RequestException on network/HTTP failure.
    """
    url = domain if domain.startswith("http") else f"https://{domain}"
    headers = {"User-Agent": settings.http_user_agent}
    response = requests.get(url, headers=headers, timeout=settings.request_timeout_seconds)
    response.raise_for_status()
    return response


def _parse_last_modified_header(response: requests.Response) -> datetime | None:
    """Parse the `Last-Modified` header into an aware datetime, if present."""
    raw_value = response.headers.get("Last-Modified")
    if not raw_value:
        return None
    try:
        parsed = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _find_date_modified_in_json(node) -> str | None:
    """Recursively search a parsed JSON-LD structure for a `dateModified` key."""
    if isinstance(node, dict):
        if "dateModified" in node and isinstance(node["dateModified"], str):
            return node["dateModified"]
        for value in node.values():
            found = _find_date_modified_in_json(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_date_modified_in_json(item)
            if found:
                return found
    return None


def _parse_schema_date_modified(html: str) -> datetime | None:
    """
    Look for a schema.org `dateModified` value in JSON-LD script blocks or
    a `<meta itemprop="dateModified">` tag, and parse it into an aware
    datetime. Returns None if no such value is found or it cannot be parsed.
    """
    soup = BeautifulSoup(html, "html.parser")
    raw_date: str | None = None

    for script_tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not script_tag.string:
            continue
        try:
            parsed_json = json.loads(script_tag.string)
        except json.JSONDecodeError:
            continue
        raw_date = _find_date_modified_in_json(parsed_json)
        if raw_date:
            break

    if not raw_date:
        meta_tag = soup.find("meta", attrs={"itemprop": "dateModified"})
        if meta_tag and meta_tag.get("content"):
            raw_date = meta_tag["content"]

    if not raw_date:
        return None

    try:
        parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _score_from_age_days(age_days: float) -> int:
    """
    Map an age in days to a 0-100 recency score using fixed thresholds.
    This is a deterministic scoring rule applied to a real, fetched date;
    it does not fabricate the underlying date itself.
    """
    if age_days <= 30:
        return 100
    if age_days <= 90:
        return 80
    if age_days <= 180:
        return 60
    if age_days <= 365:
        return 40
    if age_days <= 730:
        return 20
    return 10


def check_recency(domain: str) -> DimensionScore:
    """
    Determine how recently `domain`'s homepage was updated, based on the
    `Last-Modified` HTTP header and/or a schema.org `dateModified` value.

    Returns score == -1 ("insufficient data") when the page could not be
    fetched, or when neither signal is present. Never invents a date or a
    score in those cases.
    """
    try:
        response = _fetch_homepage(domain)
    except requests.RequestException:
        fallback = (
            f"We couldn't open the homepage at {domain} to check when it was last updated, so "
            "there's nothing real to report here. Today, try loading the homepage yourself to "
            "confirm it's reachable."
        )
        facts_summary = (
            "Dimension: whether the company's homepage shows any sign of when it was last updated.\n"
            f"Status: We could not open the homepage at {domain} to check this. It may be blocked or "
            "temporarily down."
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

    last_modified_dt = _parse_last_modified_header(response)
    schema_date_modified_dt = _parse_schema_date_modified(response.text)

    if last_modified_dt is None and schema_date_modified_dt is None:
        fallback = (
            f"We looked for any signal telling us when the homepage at {domain} was last "
            "meaningfully updated. We found none. The page simply doesn't expose that "
            "information anywhere we could check. This matters because AI search tools and "
            "search engines both favor pages that show they are kept current. A page with no "
            "update signal at all can quietly lose ground to competitors whose pages do show "
            "one. Today, ask whoever manages the site to add a visible last updated date to key "
            "pages. That also makes this easier to check next time."
        )
        facts_summary = (
            "Dimension: whether the company's homepage shows any sign of when it was last updated.\n"
            f"Status: We opened the homepage at {domain} but found no signal anywhere on it showing "
            "when it was last updated."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": (
                    "Neither a Last-Modified HTTP header nor a schema.org "
                    "dateModified value was found; recency cannot be determined."
                ),
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    # Prefer the schema.org value when both are present: it describes the
    # content itself, whereas Last-Modified can reflect unrelated
    # server/CDN behavior (e.g. dynamic pages that always report "now").
    chosen_dt = schema_date_modified_dt or last_modified_dt
    chosen_source = "schema_date_modified" if schema_date_modified_dt else "http_last_modified"

    now = datetime.now(timezone.utc)
    age_days = max((now - chosen_dt).total_seconds() / 86400, 0)
    score = _score_from_age_days(age_days)

    fallback = _build_fallback_plain_finding(age_days, score)
    facts_summary = _build_facts_summary(age_days, score)
    plain_finding, generation_log = make_plain_finding(facts_summary, fallback)

    return DimensionScore(
        dimension=DIMENSION_NAME,
        score=score,
        details={
            "status": "ok",
            "source_used": chosen_source,
            "http_last_modified": last_modified_dt.isoformat() if last_modified_dt else None,
            "schema_date_modified": schema_date_modified_dt.isoformat() if schema_date_modified_dt else None,
            "age_days": round(age_days, 1),
            "generation_log": generation_log,
            "plain_finding": plain_finding,
        },
    )


def _build_facts_summary(age_days: float, score: int) -> str:
    """Plain-text real facts handed to the AI writer — see engines._plain_finding_generator."""
    age_days_rounded = round(age_days)
    days_word = "day" if age_days_rounded == 1 else "days"
    freshness = "recent" if score >= 70 else "stale" if score <= 30 else "moderately fresh"
    return (
        "Dimension: whether the company's homepage shows any sign of when it was last updated, and "
        "how recent that update is.\n"
        f"The homepage was last meaningfully updated about {age_days_rounded} {days_word} ago, which "
        f"counts as {freshness}."
    )


def _build_fallback_plain_finding(age_days: float, score: int) -> str:
    """Founder-readable finding built only from this run's real freshness signal, used if AI writing fails."""
    age_days_rounded = round(age_days)
    days_word = "day" if age_days_rounded == 1 else "days"

    if score >= 70:
        return (
            f"The homepage shows it was last meaningfully updated about {age_days_rounded} {days_word} ago. "
            "That is recent enough that both search engines and AI search tools are likely to treat it "
            "as current. Today, just make a note of what triggered this update, so the same kind of "
            "refresh can happen again on a regular schedule."
        )
    if score <= 30:
        return (
            f"The homepage shows it was last meaningfully updated about {age_days_rounded} {days_word} ago. "
            "That is long enough that both search engines and AI search tools may start treating it as "
            "stale, and quietly favor competitors whose pages look more current. Today, make a small, "
            "genuine update to the homepage. A real change, not just a re-save, to reset that signal."
        )
    return (
        f"The homepage shows it was last meaningfully updated about {age_days_rounded} {days_word} ago. "
        "Not stale, but not fresh either. Today, consider scheduling a light homepage refresh soon, "
        "before it drifts into looking outdated to AI search tools and search engines."
    )
