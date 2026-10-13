"""
Layered diagnosis: where AI reads, and whether the company shows up there.

Takes the citations citation_visibility already collected (every URL AI
cited across the answers to the buyer questions) and, with no further
Perplexity calls and no LLM, works out for each source group (see
engines._source_layers) :

- reliance: this group's citations / all citations. How much AI leans on
  this kind of site for these questions. The data sets the weight, not us.
- presence: the citation-weighted share of the group's cited pages that
  mention the company. Every cited page is fetched (up to
  MAX_PAGES_PER_COMPANY, most-cited first) with method="full". A page that
  can't be fetched is "unknown" and left out of the denominator. For the
  company's own site, presence is simply whether AI cited it at all.
- gap: reliance x (1 - presence). The group with the biggest gap is where
  the company is missing from what AI actually reads.

Other companies' pages make up most of what AI cites, and they are not all
the same: "best tools" lists, guides and help articles, and product pages
call for different responses. They are split by page type (rules over the
URL and the page title) into the groups lists, guides and product_pages.

Fractions are used for the arithmetic so that "equal" really is equal and
ties are broken by LAYER_ORDER, never by floating point noise.

The five older diagnostic dimensions are untouched; the frontend files them
under the group they belong to (fact_consistency under review_media, the
other four under own_site).
"""

import re
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from engines._source_layers import (
    LAYER_LIST_VERSION,
    LAYER_ORDER,
    OTHER_COMPANY_GROUPS,
    PAGE_TYPE_RULES_VERSION,
    classify_host,
    classify_page_type,
    normalize_host,
)
from engines.citation_engine import _company_name_mentioned, _is_common_word_company_name
from shared._fetch_utils import extract_text_from_html
from shared.config import settings

# Bump whenever this analysis changes in a way that makes results computed
# before and after not directly comparable.
# v2: other companies split by page type; every cited page is checked.
METHOD_VERSION = "v2"

# At most this many cited pages are fetched per company, most-cited first.
MAX_PAGES_PER_COMPANY = 60

_SNIPPET_RADIUS = 90
_FETCH_WORKERS = 12
_TOP_PAGES_IN_CONCLUSION = 3

# Which dimensions belong under which group, for the frontend.
DIMENSIONS_BY_LAYER = {
    "review_media": ["fact_consistency"],
    "own_site": ["topic_relevance", "evidence_density", "content_recency", "technical_readability"],
}

# Customer-facing wording for the conclusion. Fixed templates, one per group
# that can be the biggest gap.
_GROUP_DESCRIPTIONS = {
    "reference": "reference sources such as Wikipedia, standards bodies and government or university sites",
    "review_media": "review sites and tech media",
    "community": "community sites such as Reddit, LinkedIn and YouTube",
    "lists": "'best tools' lists and comparisons written by other companies",
    "guides": "other companies' guides and help articles",
    "product_pages": "other companies' product pages",
}
_NO_SOURCES = "AI didn't show any sources for these questions, so we can't tell where it gets its information."
_OWN_SITE_NOT_CITED = "AI didn't cite your own website in any answer."
_ALL_COVERED = "For these questions, the websites AI reads do mention you."
_COULD_NOT_CHECK = "We couldn't check whether the websites AI reads mention you."


def normalize_citation_url(url: str) -> str | None:
    """Scheme dropped, host lowercase without www, no fragment, no trailing slash. Query kept (it can pick the page)."""
    host = normalize_host(url)
    if not host:
        return None
    try:
        parsed = urlparse(url if "//" in url else f"//{url}")
    except ValueError:
        return None
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{host}{path}{query}"


def _citations_per_answer(citation_details: dict) -> list[list[str]]:
    """The distinct normalized citation URLs of every successful answer (a URL cited twice in one answer counts once)."""
    answers = []
    for entry in citation_details.get("per_query_results") or []:
        for run in entry.get("runs") or []:
            if run.get("status") != "ok":
                continue
            seen, urls = set(), []
            for link in run.get("all_citations") or []:
                normalized = normalize_citation_url(link) if isinstance(link, str) else None
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    urls.append(normalized)
            answers.append(urls)
    return answers


# ---------------------------------------------------------------------------
# Does a page mention the company?
# ---------------------------------------------------------------------------

def _domain_pattern(domain: str):
    """Matches the company's domain (or a subdomain of it) in text or in a link, but not inside another word such as enclose.com."""
    own = normalize_host(domain)
    if not own:
        return None
    return re.compile(r"(?<![\w-])" + re.escape(own) + r"(?![\w-])(?!\.[a-z])", re.IGNORECASE)


def _snippet(text: str, start: int, end: int) -> str:
    left, right = max(0, start - _SNIPPET_RADIUS), min(len(text), end + _SNIPPET_RADIUS)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _heading_or_list_start_pattern(company_name: str):
    """Case-sensitive: the company name opening a heading or list item, with an optional number or bullet before it."""
    return re.compile(
        r"^\s*(?:#?\d+\s*[.):\-]?\s*|[-•*]\s*)?" + re.escape(company_name.strip()) + r"(?![A-Za-z0-9])"
    )


def analyze_page(html: str, company_name: str, domain: str) -> dict:
    """
    What one fetched page says about the company. Returns {title, mentions_you,
    rule, snippet, near_miss}.

    Companies whose name is not an ordinary English word: the company name as
    a whole word in the page text (the rule citation_visibility uses), or the
    company's domain in the text.

    Companies whose name is also an ordinary English word (Close, Linear)
    can't be matched by the word alone ("close the deal", "a linear
    process"), so a page counts only if ANY of these holds, in this order:
    a link to the company's domain; the domain in the text; the exact,
    capitalized name opening a heading (h1 to h4) or list item. No LLM.

    `near_miss` is a snippet where the name appears as a plain word on a page
    that was judged NOT to mention the company. It never affects the result;
    it is kept so the matching rules can be audited.
    """
    text = extract_text_from_html(html, method="full") or ""
    soup = BeautifulSoup(html, "html.parser")
    title = re.sub(r"\s+", " ", soup.title.get_text(" ", strip=True)).strip() if soup.title else None
    domain_re = _domain_pattern(domain)
    result = {"title": title or None, "mentions_you": False, "rule": None, "snippet": None, "near_miss": None}

    def hit(rule, snippet):
        result.update(mentions_you=True, rule=rule, snippet=snippet)
        return result

    if _is_common_word_company_name(company_name):
        if domain_re:
            for anchor in soup.find_all("a", href=True):
                match = domain_re.search(anchor["href"])
                if match:
                    return hit("domain_in_link", f"link to {anchor['href'][:160]}")
            match = domain_re.search(text)
            if match:
                return hit("domain_in_text", _snippet(text, match.start(), match.end()))
        opening = _heading_or_list_start_pattern(company_name)
        for element in soup.find_all(["h1", "h2", "h3", "h4", "li"]):
            element_text = re.sub(r"\s+", " ", element.get_text(" ", strip=True))
            if opening.match(element_text):
                return hit("heading_or_list_start", element_text[:200])
    else:
        if _company_name_mentioned(company_name, text):
            match = re.search(r"\b" + re.escape(company_name.strip()) + r"\b", text, re.IGNORECASE)
            return hit("name_in_text", _snippet(text, match.start(), match.end()))
        match = domain_re.search(text) if domain_re else None
        if match:
            return hit("domain_in_text", _snippet(text, match.start(), match.end()))

    word = re.search(r"\b" + re.escape(company_name.strip()) + r"\b", text, re.IGNORECASE)
    if word:
        result["near_miss"] = _snippet(text, word.start(), word.end())
    return result


def fetch_html(url: str) -> str:
    """The raw HTML of `url`. Raises requests.RequestException on any network or HTTP failure."""
    response = requests.get(
        url if url.startswith("http") else f"https://{url}",
        headers={"User-Agent": settings.http_user_agent},
        timeout=settings.request_timeout_seconds,
    )
    response.raise_for_status()
    return response.text


def _check_page(url: str, company_name: str, domain: str, fetch) -> dict:
    """Fetch one cited page and analyze it. Never raises: a page that can't be fetched is "unknown"."""
    try:
        html = fetch(url)
    except Exception as exc:  # any fetch problem makes the page "unknown", never a failure of the diagnosis
        return {"status": "unknown", "mentions_you": None, "rule": None, "snippet": None, "near_miss": None,
                "title": None, "error": type(exc).__name__}
    return {"status": "checked", "error": None, **analyze_page(html, company_name, domain)}


# ---------------------------------------------------------------------------
# Gaps and the conclusion
# ---------------------------------------------------------------------------

def largest_gap_layer(gaps: dict) -> str | None:
    """
    The group with the biggest positive gap. `gaps` maps group -> Fraction
    (or None when the group couldn't be checked). Ties go to the group that
    comes first in LAYER_ORDER. None when no group has a positive gap.
    """
    best, best_gap = None, Fraction(0)
    for layer in LAYER_ORDER:
        gap = gaps.get(layer)
        if gap is not None and gap > best_gap:
            best, best_gap = layer, gap
    return best


def _site_name(url: str) -> str:
    return normalize_host(url) or url


def _page_label(page: dict) -> str:
    """'“Title” (site.com)' for the conclusion; the URL stands in for a missing title."""
    title = re.sub(r"\s+", " ", page.get("title") or "").strip()
    if len(title) > 90:
        title = title[:87].rstrip() + "..."
    return f"“{title or page['url']}” ({_site_name(page['url'])})"


def top_pages_without_you(pages: list[dict], count: int = _TOP_PAGES_IN_CONCLUSION) -> list[dict]:
    """The most-cited pages that were checked and don't mention the company (ties: URL order)."""
    candidates = [p for p in pages if p["status"] == "checked" and not p["mentions_you"]]
    return sorted(candidates, key=lambda p: (-p["citations"], p["url"]))[:count]


def build_conclusion(layers: dict, biggest: str | None, total_citations: int) -> list[str]:
    """The fixed-template conclusion sentences, built only from the computed numbers and pages."""
    if total_citations == 0:
        return [_NO_SOURCES]

    sentences = []
    if biggest:
        layer = layers[biggest]
        verb = "mostly reads" if layer["reliance_fraction"] > Fraction(1, 2) else "often reads"
        top = top_pages_without_you(layer["pages"])
        listing = ", ".join(_page_label(p) for p in top)
        if layer["presence_fraction"] == 0:
            tail = f"the top ones don't mention you: {listing}." if listing else "they don't mention you."
        else:
            share = round(layer["presence_fraction"] * 100)
            tail = f"only {share}% of them mention you." + (f" The most cited ones that don't: {listing}." if listing else "")
        sentences.append(f"For these questions, AI {verb} {_GROUP_DESCRIPTIONS[biggest]}, and {tail}")
    elif any(l["citations"] and l["presence_fraction"] is None for l in layers.values()):
        sentences.append(_COULD_NOT_CHECK)
    else:
        sentences.append(_ALL_COVERED)

    if layers["own_site"]["citations"] == 0:
        sentences.append(_OWN_SITE_NOT_CITED)
    return sentences


def _status(layer: dict, biggest: str | None, name: str) -> str:
    if layer["citations"] == 0:
        return "not_used"
    if layer["gap_fraction"] is None:
        return "unknown"
    if name == biggest:
        return "biggest_gap"
    return "gap" if layer["gap_fraction"] > 0 else "covered"


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def build_source_layers(company_name: str, domain: str, citation_details: dict, fetch=fetch_html) -> dict | None:
    """
    The layered diagnosis for one company, from citation_visibility's
    details. Returns None when there are no successful answers at all
    (nothing to analyze). `fetch(url) -> html` is injectable for tests.
    """
    own_domain = normalize_host(domain) or ""
    answers = _citations_per_answer(citation_details)
    if not answers:
        return None

    # citation counts per page and per domain, by the page's site layer
    page_counts: dict[str, int] = {}
    for urls in answers:
        for url in urls:
            page_counts[url] = page_counts.get(url, 0) + 1
    total_citations = sum(page_counts.values())
    site_layer = {url: classify_host(normalize_host(url), own_domain) for url in page_counts}

    # check the most-cited pages (own site excluded: a citation of it is presence by itself)
    candidates = sorted((u for u in page_counts if site_layer[u] != "own_site"), key=lambda u: (-page_counts[u], u))
    to_fetch = candidates[:MAX_PAGES_PER_COMPANY]
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as executor:
        checks = dict(zip(to_fetch, executor.map(lambda u: _check_page(u, company_name, domain, fetch), to_fetch)))

    # assign every page to a group; other companies' pages by page type
    pages_by_group: dict[str, list[dict]] = {layer: [] for layer in LAYER_ORDER}
    domains_by_group: dict[str, dict[str, int]] = {layer: {} for layer in LAYER_ORDER}
    for url, count in page_counts.items():
        if site_layer[url] == "own_site":
            record = {"status": "cited", "mentions_you": True, "rule": "own_site", "snippet": None, "near_miss": None,
                      "title": None, "error": None}
        elif url in checks:
            record = checks[url]
        else:
            record = {"status": "not_checked", "mentions_you": None, "rule": None, "snippet": None, "near_miss": None,
                      "title": None, "error": None}

        group = site_layer[url]
        page_type = None
        if group == "other_companies":
            group = page_type = classify_page_type(url, record.get("title"))
        pages_by_group[group].append({"url": url, "citations": count, "page_type": page_type, **record})
        host = normalize_host(url)
        domains_by_group[group][host] = domains_by_group[group].get(host, 0) + count

    layers: dict[str, dict] = {}
    for name in LAYER_ORDER:
        pages = sorted(pages_by_group[name], key=lambda p: (-p["citations"], p["url"]))
        citations = sum(p["citations"] for p in pages)
        reliance = Fraction(citations, total_citations) if total_citations else Fraction(0)
        known = [p for p in pages if p["status"] == "checked"]
        checked_citations = sum(p["citations"] for p in known) if name != "own_site" else citations
        denominator = sum(p["citations"] for p in known)

        if citations == 0:
            presence = None
        elif name == "own_site":
            presence = Fraction(1)
        else:
            presence = Fraction(sum(p["citations"] for p in known if p["mentions_you"]), denominator) if denominator else None

        if citations == 0:
            gap = Fraction(0)
        else:
            gap = reliance * (1 - presence) if presence is not None else None

        layers[name] = {
            "citations": citations,
            "reliance_fraction": reliance,
            "presence_fraction": presence,
            "gap_fraction": gap,
            "pages": pages,
            "checked_citations": checked_citations,
            "domains": [
                {"domain": host, "citations": count}
                for host, count in sorted(domains_by_group[name].items(), key=lambda item: (-item[1], item[0]))
            ],
        }

    biggest = largest_gap_layer({name: layer["gap_fraction"] for name, layer in layers.items()})
    conclusion = build_conclusion(layers, biggest, total_citations)

    output_layers = []
    for name in LAYER_ORDER:
        layer = layers[name]
        pages = layer["pages"]
        output_layers.append(
            {
                "layer": name,
                "parent": "other_companies" if name in OTHER_COMPANY_GROUPS else name,
                "citations": layer["citations"],
                "reliance": round(float(layer["reliance_fraction"]), 4),
                "presence": None if layer["presence_fraction"] is None else round(float(layer["presence_fraction"]), 4),
                "gap": None if layer["gap_fraction"] is None else round(float(layer["gap_fraction"]), 4),
                "status": _status(layer, biggest, name),
                "coverage": {
                    "checked_citations": layer["checked_citations"],
                    "citations": layer["citations"],
                    "ratio": _ratio(layer["checked_citations"], layer["citations"]),
                },
                "pages": pages,
                "pages_not_checked": sum(1 for p in pages if p["status"] == "not_checked"),
                "unknown_pages": [
                    {"url": p["url"], "citations": p["citations"], "error": p["error"]} for p in pages if p["status"] == "unknown"
                ],
                "domains": layer["domains"],
            }
        )

    scored = [l for l in layers.values()]
    checked_total = sum(
        p["citations"] for l in scored for p in l["pages"] if p["status"] == "checked"
    )
    fetchable_total = sum(p["citations"] for l in scored for p in l["pages"] if p["status"] != "cited")
    return {
        "list_version": LAYER_LIST_VERSION,
        "page_type_rules_version": PAGE_TYPE_RULES_VERSION,
        "method_version": METHOD_VERSION,
        "total_answers": len(answers),
        "total_citations": total_citations,
        "pages_cited": len(page_counts),
        "pages_checked": sum(1 for u in checks if checks[u]["status"] == "checked"),
        "pages_cap": MAX_PAGES_PER_COMPANY,
        "coverage": {
            "checked_citations": checked_total,
            "citations_outside_own_site": fetchable_total,
            "ratio": _ratio(checked_total, fetchable_total),
        },
        "biggest_gap_layer": biggest,
        "conclusion": conclusion,
        "layers": output_layers,
    }
