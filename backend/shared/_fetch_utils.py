"""
Shared HTTP fetching helper for engines that need the visible body text of
a web page (evidence_engine, fact_engine). Kept in one place so both
engines fetch and clean HTML the same way instead of duplicating it.

This is a plain `requests` GET + BeautifulSoup extraction, which is the
simplest possible approach. Some sites block simple HTTP clients with
anti-bot measures (Cloudflare challenges, JS-only rendering, etc.). If
that becomes a real problem, a future improvement is to swap this fetch
step for a service like the Jina AI Reader API (https://jina.ai/reader/),
which handles JS rendering and anti-bot bypassing server-side and returns
clean article text directly. That integration is intentionally NOT
implemented here to keep this dependency-light.
"""

import re

import requests
from bs4 import BeautifulSoup

from shared.config import settings


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

    soup = BeautifulSoup(response.text, "html.parser")

    # Drop non-visible / non-content elements before extracting text.
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text
