"""
Technical layer optimization package: JSON-LD structured data, an
llms.txt file, and robots.txt suggestions for a domain.

Pure rule-based generation, no LLM calls anywhere in this module:
everything here is extracted directly from the page's own HTML (title,
meta tags, nav links) or is a fixed recommendation template built from a
real robots.txt fetch. No field is guessed or invented — if a signal
isn't actually present on the page, the corresponding field is omitted
rather than filled with a plausible guess.

Deliberately a separate module from answer_capsule_engine.py (which
calls an LLM): this generation is fast and has no external-API failure
mode, so it must keep working even if the LLM-backed capsule generation
is slow, times out, or errors. See server.py, where the two live behind
separate endpoints for exactly this reason.
"""

from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from shared._fetch_utils import fetch_page_text
from shared.config import settings

# The AI crawlers this package checks robots.txt against.
AI_CRAWLER_USER_AGENTS = ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"]

_SOCIAL_DOMAINS = ("twitter.com", "x.com", "linkedin.com", "facebook.com", "github.com", "youtube.com", "instagram.com")

# Nav/footer link text or URL slugs that signal an "important" page worth
# listing in llms.txt, matched case-insensitively.
_IMPORTANT_PAGE_KEYWORDS = [
    "pricing", "plans", "product", "products", "features", "solutions",
    "about", "docs", "documentation", "blog", "contact", "customers",
    "case-stud", "case studies", "security", "compliance",
]
MAX_IMPORTANT_PAGES = 6


def _fetch_soup(url: str) -> tuple[BeautifulSoup, str]:
    """
    Fetch `url` and return (parsed soup, final resolved URL) for
    structural parsing. Raises requests.RequestException on failure.
    """
    full_url = url if url.startswith("http") else f"https://{url}"
    headers = {"User-Agent": settings.http_user_agent}
    response = requests.get(full_url, headers=headers, timeout=settings.request_timeout_seconds)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser"), response.url


def _meta_content(soup: BeautifulSoup, *names: str) -> str | None:
    """Return the first non-empty content of a <meta name=...> or <meta property=...> tag matching any of `names`."""
    for name in names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        if tag and tag.get("content") and tag["content"].strip():
            return tag["content"].strip()
    return None


def _extract_title(soup: BeautifulSoup) -> str | None:
    if soup.title and soup.title.string:
        text = soup.title.string.strip()
        return text or None
    return None


def _extract_logo(soup: BeautifulSoup, base_url: str) -> str | None:
    icon = soup.find("link", rel=lambda v: v and "icon" in v.lower())
    if icon and icon.get("href"):
        return urljoin(base_url, icon["href"])
    og_image = _meta_content(soup, "og:image")
    if og_image:
        return urljoin(base_url, og_image)
    return None


def _extract_same_as(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Real social profile links found on the page, for schema.org sameAs."""
    links = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        host = urlparse(href).netloc.lower()
        if any(social in host for social in _SOCIAL_DOMAINS):
            links.add(href.split("?")[0])
    return sorted(links)[:8]


def _has_pricing_page(soup: BeautifulSoup) -> bool:
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        href = a["href"].lower()
        if "pricing" in text or "pricing" in href or "plans" in text or "plans" in href:
            return True
    return False


def _normalized_path(url: str) -> str:
    """Path with a trailing 'index.html' and trailing slash stripped, so '/', '/index.html', and '' all compare equal."""
    path = urlparse(url).path
    if path.endswith("index.html"):
        path = path[: -len("index.html")]
    return path.rstrip("/")


def _extract_important_pages(soup: BeautifulSoup, base_url: str) -> dict[str, str]:
    """Return {url: link_text} for internal links whose text/URL matches an important-page keyword."""
    base_host = urlparse(base_url).netloc
    base_path = _normalized_path(base_url)
    seen: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if not text:
            continue
        href = urljoin(base_url, a["href"])
        parsed = urlparse(href)
        if parsed.netloc != base_host:
            continue  # only list pages on this same site
        text_lower = text.lower()
        href_lower = href.lower()
        if any(kw in text_lower or kw in href_lower for kw in _IMPORTANT_PAGE_KEYWORDS):
            clean_url = href.split("#")[0]
            # Skip links that just point back to the homepage under a
            # different URL form ('/', '/index.html', no path at all) —
            # otherwise the homepage can get listed twice under two
            # "different" URLs that are really the same page.
            if clean_url not in seen and _normalized_path(clean_url) != base_path:
                seen[clean_url] = text
        if len(seen) >= MAX_IMPORTANT_PAGES:
            break
    return seen


def _describe_page(url: str) -> dict | None:
    """
    Best-effort real description for one internal page: its own meta
    description if present, else a snippet of its actual visible text.
    Returns None if the page can't be fetched at all — that page is then
    silently skipped rather than failing the whole package.
    """
    try:
        soup, final_url = _fetch_soup(url)
    except requests.RequestException:
        return None

    description = _meta_content(soup, "description", "og:description")
    title = _extract_title(soup)

    if not description:
        text = fetch_page_text(url)
        if text:
            snippet = text[:160]
            # Trim to the last whole word so we don't cut mid-word.
            if len(text) > 160:
                snippet = snippet.rsplit(" ", 1)[0] + "…"
            description = snippet or None

    return {"url": final_url, "title": title, "description": description}


def _build_json_ld(soup: BeautifulSoup, base_url: str, company_name: str) -> dict:
    description = _meta_content(soup, "description", "og:description")
    logo = _extract_logo(soup, base_url)
    same_as = _extract_same_as(soup, base_url)

    organization = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": company_name,
        "url": base_url,
    }
    if description:
        organization["description"] = description
    if logo:
        organization["logo"] = logo
    if same_as:
        organization["sameAs"] = same_as

    # SoftwareApplication is only emitted when there is a real, checkable
    # signal this is a software product (a pricing/plans page actually
    # exists) — never emitted purely on the assumption that a B2B site is
    # selling software, since that would be a guess.
    software_application = None
    note = None
    if _has_pricing_page(soup) and description:
        software_application = {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": company_name,
            "url": base_url,
            "description": description,
        }
    else:
        note = (
            "No SoftwareApplication block generated: no real pricing or plans page was found on the "
            "site to justify labeling this a software product, and guessing would violate the "
            "no-fabrication rule. Only the Organization block is included."
        )

    return {
        "status": "ok",
        "organization": organization,
        "software_application": software_application,
        "note": note,
    }


def _build_llms_txt(soup: BeautifulSoup, base_url: str, company_name: str) -> dict:
    homepage_description = _meta_content(soup, "description", "og:description")
    important_page_links = _extract_important_pages(soup, base_url)

    pages = [
        {
            "url": base_url,
            "title": _extract_title(soup) or company_name,
            "description": homepage_description,
        }
    ]
    for url in important_page_links:
        info = _describe_page(url)
        if info:
            pages.append(info)

    lines = [f"# {company_name}", ""]
    if homepage_description:
        lines.append(f"> {homepage_description}")
        lines.append("")
    lines.append("## Pages")
    lines.append("")
    for page in pages:
        title = page.get("title") or page["url"]
        description = page.get("description")
        if description:
            lines.append(f"- [{title}]({page['url']}): {description}")
        else:
            lines.append(f"- [{title}]({page['url']})")

    return {
        "status": "ok",
        "content": "\n".join(lines),
        "pages_included": len(pages),
    }


def generate_robots_suggestions(domain: str) -> dict:
    """
    Fetch this domain's real robots.txt and check whether the AI search
    crawlers in AI_CRAWLER_USER_AGENTS can fetch the homepage, using
    urllib.robotparser (the standard library's own robots.txt semantics,
    not a hand-rolled parser). Only ADDITIONS are suggested for whichever
    crawlers are currently blocked; crawlers already allowed are left
    alone, and nothing here proposes replacing the existing file.
    """
    full_url = domain if domain.startswith("http") else f"https://{domain}"
    robots_url = full_url.rstrip("/") + "/robots.txt"

    try:
        headers = {"User-Agent": settings.http_user_agent}
        response = requests.get(robots_url, headers=headers, timeout=settings.request_timeout_seconds)
    except requests.RequestException as exc:
        return {"status": "insufficient_data", "reason": f"Could not fetch {robots_url}: {exc}"}

    if response.status_code == 404:
        existing_content = ""
        robots_txt_found = False
    else:
        response.raise_for_status()
        existing_content = response.text
        robots_txt_found = True

    parser = RobotFileParser()
    parser.parse(existing_content.splitlines())

    allowed, blocked = [], []
    for agent in AI_CRAWLER_USER_AGENTS:
        (allowed if parser.can_fetch(agent, full_url + "/") else blocked).append(agent)

    suggested_additions = None
    if blocked:
        lines = [
            "# Add the lines below to allow AI search crawlers to index this site.",
            "# These are additions to append to the existing file, not a full replacement.",
            "",
        ]
        for agent in blocked:
            lines.append(f"User-agent: {agent}")
            lines.append("Allow: /")
            lines.append("")
        suggested_additions = "\n".join(lines).strip()

    return {
        "status": "ok",
        "robots_txt_found": robots_txt_found,
        "already_allowed": allowed,
        "currently_blocked_or_unspecified": blocked,
        "suggested_additions": suggested_additions,
    }


def generate_technical_package(domain: str, company_name: str) -> dict:
    """
    Top-level entry point: JSON-LD, llms.txt, and robots.txt suggestions
    for `domain`. The homepage is fetched once and its parsed structure
    reused for both JSON-LD and llms.txt; robots.txt is checked with its
    own separate, tiny request regardless of whether the homepage fetch
    succeeds, since it doesn't depend on homepage content.
    """
    full_url = domain if domain.startswith("http") else f"https://{domain}"
    robots_txt = generate_robots_suggestions(domain)

    try:
        soup, final_url = _fetch_soup(domain)
    except requests.RequestException as exc:
        reason = f"Could not open {domain} to generate this part of the technical package: {exc}"
        return {
            "json_ld": {"status": "insufficient_data", "reason": reason},
            "llms_txt": {"status": "insufficient_data", "reason": reason},
            "robots_txt": robots_txt,
        }

    return {
        "json_ld": _build_json_ld(soup, final_url, company_name),
        "llms_txt": _build_llms_txt(soup, final_url, company_name),
        "robots_txt": robots_txt,
    }
