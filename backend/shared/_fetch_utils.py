"""
Shared HTTP fetching helper for engines that need the visible body text of
a web page (evidence_engine, fact_engine, citation_engine, and
agent_service's answer_capsule_engine/technical_package_engine). Kept in
one place so every caller fetches and extracts text the same way instead
of duplicating it.

The network step is a plain `requests` GET, unchanged — the request
itself is not routed through trafilatura's own downloader, only its HTML
extraction is used. Some sites block simple HTTP clients with anti-bot
measures (Cloudflare challenges, JS-only rendering, etc.). If that
becomes a real problem, a future improvement is to swap this fetch step
for a service like the Jina AI Reader API (https://jina.ai/reader/),
which handles JS rendering and anti-bot bypassing server-side. That
integration is intentionally NOT implemented here to keep this
dependency-light.

Text extraction is trafilatura first (it targets the actual article/body
content and excludes boilerplate like nav bars and footers far better
than a blanket tag-strip), falling back to the original whole-page
BeautifulSoup extraction whenever trafilatura comes back empty or with
too little text to be useful — e.g. a page structure it doesn't
recognize. This engine depends on getting SOME real text back rather
than none at all, so the fallback exists specifically for that case.
"""

import re

import requests
import trafilatura
from bs4 import BeautifulSoup

from shared.config import settings

# A trafilatura extraction shorter than this (after whitespace collapse)
# is treated as "too little to trust" and the BeautifulSoup fallback is
# used instead, rather than running an engine on a near-empty string.
MIN_EXTRACTED_CHARS = 200


def _extract_text_with_beautifulsoup(html: str) -> str:
    """The original whole-page extraction: strip non-content tags, then take all remaining visible text."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def extract_text_from_html_with_method(html: str) -> tuple[str, str]:
    """
    Extract the main body text from raw HTML, returning (text, method)
    so callers that care can see which approach actually produced the
    text: "trafilatura" or "beautifulsoup_fallback". Never raises on
    empty or malformed HTML; worst case both approaches return "".
    """
    extracted = trafilatura.extract(html)
    if extracted:
        collapsed = re.sub(r"\s+", " ", extracted).strip()
        if len(collapsed) >= MIN_EXTRACTED_CHARS:
            return collapsed, "trafilatura"

    return _extract_text_with_beautifulsoup(html), "beautifulsoup_fallback"


def extract_text_from_html(html: str) -> str:
    """Pure function: raw HTML in, extracted visible body text out. See extract_text_from_html_with_method for which method was used."""
    text, _method = extract_text_from_html_with_method(html)
    return text


def fetch_page_text(url: str) -> str:
    """
    Fetch `url` (an https:// scheme is added if missing) and return its
    extracted visible body text. Raises requests.RequestException on
    network/HTTP failure.
    """
    full_url = url if url.startswith("http") else f"https://{url}"
    headers = {"User-Agent": settings.http_user_agent}
    response = requests.get(full_url, headers=headers, timeout=settings.request_timeout_seconds)
    response.raise_for_status()

    return extract_text_from_html(response.text)
