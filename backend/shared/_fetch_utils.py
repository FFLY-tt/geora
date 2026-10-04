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

Text extraction has two modes, picked per call via the `method`
parameter:

- "main" (the default): trafilatura first, since it targets the actual
  article/body content and excludes boilerplate like nav bars and
  footers far better than a blanket tag-strip. Falls back to the
  original whole-page BeautifulSoup extraction whenever trafilatura
  comes back empty or with too little text to be useful.
- "full": always the whole-page BeautifulSoup extraction, skipping
  trafilatura entirely. Investigation (see evidence_engine.py's
  docstring) found trafilatura treats homepage testimonial quotes and
  stat cards as boilerplate and drops them — on close.com this dropped
  evidence_density's score by ~16 points, confirmed across repeated
  calls, not AI scoring noise. For a caller where exactly that dropped
  content (named customer quotes, "$100,000 saved", "50% increase")
  IS the evidence being judged, "full" is the correct mode, not a
  fallback.
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


def extract_text_from_html_with_method(html: str, method: str = "main") -> tuple[str, str]:
    """
    Extract body text from raw HTML, returning (text, used_method).

    `method="main"` (default) tries trafilatura first and only falls
    back to BeautifulSoup if it comes back empty or too short;
    `method="full"` skips trafilatura entirely and always uses the
    whole-page BeautifulSoup extraction. See this module's docstring
    for why "full" exists — it is a deliberate mode, not a fallback.

    `used_method` is one of "trafilatura", "beautifulsoup_fallback"
    (method="main" but trafilatura was empty/too short), or
    "beautifulsoup_full" (method="full" was requested directly). Never
    raises on empty or malformed HTML; worst case both approaches
    return "".
    """
    if method == "full":
        return _extract_text_with_beautifulsoup(html), "beautifulsoup_full"

    extracted = trafilatura.extract(html)
    if extracted:
        collapsed = re.sub(r"\s+", " ", extracted).strip()
        if len(collapsed) >= MIN_EXTRACTED_CHARS:
            return collapsed, "trafilatura"

    return _extract_text_with_beautifulsoup(html), "beautifulsoup_fallback"


def extract_text_from_html(html: str, method: str = "main") -> str:
    """Pure function: raw HTML in, extracted visible body text out. See extract_text_from_html_with_method for `method` and which approach was actually used."""
    text, _used_method = extract_text_from_html_with_method(html, method=method)
    return text


def fetch_page_text(url: str, method: str = "main") -> str:
    """
    Fetch `url` (an https:// scheme is added if missing) and return its
    extracted visible body text. `method` is passed straight through to
    extract_text_from_html — see that function and this module's
    docstring. Raises requests.RequestException on network/HTTP failure.
    """
    full_url = url if url.startswith("http") else f"https://{url}"
    headers = {"User-Agent": settings.http_user_agent}
    response = requests.get(full_url, headers=headers, timeout=settings.request_timeout_seconds)
    response.raise_for_status()

    return extract_text_from_html(response.text, method=method)
