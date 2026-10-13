"""
Layered diagnosis: where AI reads, and whether the company shows up there.

Takes the citations citation_visibility already collected (every URL AI
cited across the answers to the buyer questions) and, with no further
Perplexity calls and no LLM, works out for each source layer (see
engines._source_layers):

- reliance: this layer's citations / all citations. How much AI leans on
  this kind of site for these questions. The data sets the weight, not us.
- presence: for the layer's most-cited pages (at most MAX_PAGES_PER_LAYER),
  the share of citations that point at a page which mentions the company,
  fetched with method="full". A page that can't be fetched is "unknown"
  and left out of the denominator. For the company's own site, presence
  is simply whether AI cited it at all.
- gap: reliance x (1 - presence). The layer with the biggest gap is where
  the company is missing from what AI actually reads.

Fractions are used for the arithmetic so that "equal" really is equal and
ties are broken by LAYER_ORDER, never by floating point noise.

The five older diagnostic dimensions are untouched; the frontend files them
under the layer they belong to (fact_consistency under review_media, the
other four under own_site).
"""

import re
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from urllib.parse import urlparse

from engines._source_layers import (
    LAYER_LIST_VERSION,
    LAYER_ORDER,
    classify_host,
    normalize_host,
)
from engines.citation_engine import _company_name_mentioned, _is_common_word_company_name
from shared._fetch_utils import fetch_page_text

# Bump whenever this analysis changes in a way that makes results computed
# before and after not directly comparable.
METHOD_VERSION = "v1"

# At most this many pages per layer are fetched (the most-cited ones).
MAX_PAGES_PER_LAYER = 5

_SNIPPET_RADIUS = 90

# Which dimensions belong under which layer, for the frontend.
DIMENSIONS_BY_LAYER = {
    "review_media": ["fact_consistency"],
    "own_site": ["topic_relevance", "evidence_density", "content_recency", "technical_readability"],
}

# Customer-facing wording for the conclusion. Fixed templates.
_LAYER_DESCRIPTIONS = {
    "reference": "reference sources such as Wikipedia, standards bodies and government or university sites",
    "review_media": "review sites and tech media",
    "community": "community sites such as Reddit, LinkedIn and YouTube",
    "other_companies": "other companies' websites",
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


def page_mentions_company(company_name: str, domain: str, text: str) -> str | None:
    """
    The snippet of `text` that mentions the company, or None. Uses the
    same whole-word name rule as citation_visibility. A company whose name
    is also an ordinary English word (Close, Linear) only counts when its
    domain appears in the text: that rule is stricter than citation_visibility's
    (which asks an LLM), because this analysis must stay deterministic.
    """
    candidates = []
    if not _is_common_word_company_name(company_name) and _company_name_mentioned(company_name, text):
        candidates.append(re.compile(r"\b" + re.escape(company_name.strip()) + r"\b", re.IGNORECASE))
    own = normalize_host(domain)
    if own:
        candidates.append(re.compile(r"(?<![\w.-])" + re.escape(own) + r"(?![\w-])", re.IGNORECASE))
    for pattern in candidates:
        match = pattern.search(text)
        if match:
            start, end = max(0, match.start() - _SNIPPET_RADIUS), min(len(text), match.end() + _SNIPPET_RADIUS)
            return re.sub(r"\s+", " ", text[start:end]).strip()
    return None


def _check_page(url: str, company_name: str, domain: str, fetch_text) -> dict:
    """Fetch one cited page and decide whether it mentions the company. Never raises."""
    try:
        text = fetch_text(url if url.startswith("http") else f"https://{url}", method="full")
    except Exception as exc:  # any fetch problem makes the page "unknown", never a failure of the diagnosis
        return {"status": "unknown", "mentions_you": None, "snippet": None, "error": type(exc).__name__}
    snippet = page_mentions_company(company_name, domain, text or "")
    return {"status": "checked", "mentions_you": snippet is not None, "snippet": snippet, "error": None}


def largest_gap_layer(gaps: dict) -> str | None:
    """
    The layer with the biggest positive gap. `gaps` maps layer -> Fraction
    (or None when the layer couldn't be checked). Ties go to the layer that
    comes first in LAYER_ORDER. None when no layer has a positive gap.
    """
    best, best_gap = None, Fraction(0)
    for layer in LAYER_ORDER:
        gap = gaps.get(layer)
        if gap is not None and gap > best_gap:
            best, best_gap = layer, gap
    return best


def build_conclusion(layers: dict, biggest: str | None, total_citations: int) -> list[str]:
    """The fixed-template conclusion sentences, built only from the computed numbers."""
    if total_citations == 0:
        return [_NO_SOURCES]

    sentences = []
    if biggest:
        layer = layers[biggest]
        verb = "mostly reads" if layer["reliance_fraction"] > Fraction(1, 2) else "often reads"
        if layer["presence_fraction"] == 0:
            tail = "they don't mention you."
        else:
            tail = f"only {round(layer['presence_fraction'] * 100)}% of what it reads there mentions you."
        sentences.append(f"For these questions, AI {verb} {_LAYER_DESCRIPTIONS[biggest]}, and {tail}")
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


def build_source_layers(company_name: str, domain: str, citation_details: dict, fetch_text=fetch_page_text) -> dict | None:
    """
    The layered diagnosis for one company, from citation_visibility's
    details. Returns None when there are no successful answers at all (nothing
    to analyze). `fetch_text(url, method="full")` is injectable for tests.
    """
    own_domain = normalize_host(domain) or ""
    answers = _citations_per_answer(citation_details)
    if not answers:
        return None

    # citation counts per layer and per page, and per domain for the audit list
    page_counts: dict[str, dict[str, int]] = {layer: {} for layer in LAYER_ORDER}
    domain_counts: dict[str, dict[str, int]] = {layer: {} for layer in LAYER_ORDER}
    for urls in answers:
        for url in urls:
            host = normalize_host(url)
            layer = classify_host(host, own_domain)
            page_counts[layer][url] = page_counts[layer].get(url, 0) + 1
            domain_counts[layer][host] = domain_counts[layer].get(host, 0) + 1

    layer_citations = {layer: sum(page_counts[layer].values()) for layer in LAYER_ORDER}
    total_citations = sum(layer_citations.values())

    # pick and check the most-cited pages of every layer except own_site
    top_pages = {
        layer: sorted(page_counts[layer].items(), key=lambda item: (-item[1], item[0]))[:MAX_PAGES_PER_LAYER]
        for layer in LAYER_ORDER
    }
    to_fetch = [(layer, url) for layer in LAYER_ORDER if layer != "own_site" for url, _ in top_pages[layer]]
    with ThreadPoolExecutor(max_workers=8) as executor:
        checks = dict(zip(to_fetch, executor.map(lambda item: _check_page(item[1], company_name, domain, fetch_text), to_fetch)))

    layers: dict[str, dict] = {}
    for layer in LAYER_ORDER:
        citations = layer_citations[layer]
        reliance = Fraction(citations, total_citations) if total_citations else Fraction(0)
        pages = []
        for url, count in top_pages[layer]:
            if layer == "own_site":
                page = {"status": "cited", "mentions_you": True, "snippet": None, "error": None}
            else:
                page = checks[(layer, url)]
            pages.append({"url": url, "citations": count, **page})

        # How many of this layer's citations the presence figure is based on:
        # only the most-cited pages are checked, so for a big layer it is a sample.
        checked_citations = sum(p["citations"] for p in pages if p["status"] in ("checked", "cited"))
        if citations == 0:
            presence = None
        elif layer == "own_site":
            presence = Fraction(1)
        else:
            known = [p for p in pages if p["status"] == "checked"]
            denominator = sum(p["citations"] for p in known)
            presence = Fraction(sum(p["citations"] for p in known if p["mentions_you"]), denominator) if denominator else None

        if citations == 0:
            gap = Fraction(0)
        else:
            gap = reliance * (1 - presence) if presence is not None else None

        layers[layer] = {
            "layer": layer,
            "citations": citations,
            "reliance_fraction": reliance,
            "presence_fraction": presence,
            "gap_fraction": gap,
            "pages": pages,
            "checked_citations": checked_citations,
            "unknown_pages": [{"url": p["url"], "citations": p["citations"], "error": p["error"]} for p in pages if p["status"] == "unknown"],
            "pages_not_checked": max(0, len(page_counts[layer]) - len(pages)),
            "domains": [
                {"domain": host, "citations": count}
                for host, count in sorted(domain_counts[layer].items(), key=lambda item: (-item[1], item[0]))
            ],
        }

    biggest = largest_gap_layer({name: layer["gap_fraction"] for name, layer in layers.items()})
    conclusion = build_conclusion(layers, biggest, total_citations)

    output_layers = []
    for name in LAYER_ORDER:
        layer = layers[name]
        output_layers.append(
            {
                "layer": name,
                "citations": layer["citations"],
                "reliance": round(float(layer["reliance_fraction"]), 4),
                "presence": None if layer["presence_fraction"] is None else round(float(layer["presence_fraction"]), 4),
                "gap": None if layer["gap_fraction"] is None else round(float(layer["gap_fraction"]), 4),
                "status": _status(layer, biggest, name),
                "pages": layer["pages"],
                "checked_citations": layer["checked_citations"],
                "unknown_pages": layer["unknown_pages"],
                "pages_not_checked": layer["pages_not_checked"],
                "domains": layer["domains"],
            }
        )

    return {
        "list_version": LAYER_LIST_VERSION,
        "method_version": METHOD_VERSION,
        "total_answers": len(answers),
        "total_citations": total_citations,
        "biggest_gap_layer": biggest,
        "conclusion": conclusion,
        "layers": output_layers,
    }
