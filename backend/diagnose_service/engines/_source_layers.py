"""
Source layers: which KIND of website an AI answer's citation points to.

Why this exists: AI assistants answering a customer's question read many
sites, and the company's own site is only one of them, usually a small
one. Diagnosing only the company's website therefore explains little of
why an AI does or doesn't mention it. Citations are grouped into layers
so the report can show how much AI relies on each kind of site and
whether the company shows up there.

The domain lists below are OUR judgment, not an outside standard. They are
plain constants with a version (LAYER_LIST_VERSION) so a classification
can be reproduced exactly and the lists can be extended later; bump the
version whenever any list changes. A site that isn't on a list is never
guessed at: it goes to "other_companies".

Pure and offline: standard library only, no network, no AI calls.
"""

import re
from urllib.parse import urlparse

# Bump whenever any domain list or domain rule below changes.
# v2: news sites, more review media, community.* subdomains, eff.org.
LAYER_LIST_VERSION = "v2"

# Bump whenever the page type keywords or rules below change.
PAGE_TYPE_RULES_VERSION = "v1"

# Fixed order used to break ties (see source_layers.largest_gap_layer) and
# to order the groups in the report. other_companies is split by page type
# into lists, guides and product_pages, and those three take its place.
LAYER_ORDER = ["reference", "review_media", "community", "own_site", "lists", "guides", "product_pages"]
OTHER_COMPANY_GROUPS = ("lists", "guides", "product_pages")

# reference: encyclopedias, standards bodies, industry bodies. Government
# (.gov) and university (.edu) sites are matched by REFERENCE_TLD_PATTERN.
REFERENCE_DOMAINS = (
    "acm.org",
    "aicpa.org",
    "britannica.com",
    "cloudsecurityalliance.org",
    "europa.eu",
    "eff.org",
    "ieee.org",
    "ietf.org",
    "iso.org",
    "mitre.org",
    "owasp.org",
    "pcisecuritystandards.org",
    "w3.org",
    "who.int",
    "wikimedia.org",
    "wikipedia.org",
)
# .gov and .edu, plus their country forms such as .gov.uk and .edu.au.
REFERENCE_TLD_PATTERN = re.compile(r"(?:^|\.)(?:gov|edu)(?:\.[a-z]{2})?$")

# review_media: software review sites, analyst firms, and common tech and
# industry media.
REVIEW_MEDIA_DOMAINS = (
    "apnews.com",
    "arstechnica.com",
    "bbc.co.uk",
    "bbc.com",
    "bleepingcomputer.com",
    "bloomberg.com",
    "businessinsider.com",
    "businesswire.com",
    "capterra.com",
    "cmswire.com",
    "cnbc.com",
    "cnet.com",
    "cnn.com",
    "computerworld.com",
    "crn.com",
    "csoonline.com",
    "darkreading.com",
    "entrepreneur.com",
    "fastcompany.com",
    "fitsmallbusiness.com",
    "forbes.com",
    "forrester.com",
    "ft.com",
    "gartner.com",
    "getapp.com",
    "g2.com",
    "globenewswire.com",
    "idc.com",
    "inc.com",
    "infoworld.com",
    "laptopjudge.com",
    "nerdwallet.com",
    "nytimes.com",
    "pcmag.com",
    "peerspot.com",
    "prnewswire.com",
    "producthunt.com",
    "reuters.com",
    "sanluisobispo.com",
    "securityweek.com",
    "siliconangle.com",
    "smallbiztrends.com",
    "smashingmagazine.com",
    "softwareadvice.com",
    "sourceforge.net",
    "techbullion.com",
    "techcrunch.com",
    "techradar.com",
    "techtarget.com",
    "theguardian.com",
    "theregister.com",
    "thehackernews.com",
    "theverge.com",
    "trustpilot.com",
    "trustradius.com",
    "usatoday.com",
    "venturebeat.com",
    "washingtonpost.com",
    "wired.com",
    "wsj.com",
    "zdnet.com",
)

# community: places where people talk, ask, and post.
COMMUNITY_DOMAINS = (
    "community.atlassian.com",
    "linkedin.com",
    "medium.com",
    "quora.com",
    "reddit.com",
    "stackexchange.com",
    "stackoverflow.com",
    "youtu.be",
    "youtube.com",
)


def normalize_host(url_or_host: str) -> str | None:
    """Lowercase hostname without a leading "www." or a port, or None if there is none."""
    value = (url_or_host or "").strip()
    if not value:
        return None
    try:
        parsed = urlparse(value if "//" in value else f"//{value}")
    except ValueError:
        return None
    host = parsed.netloc.rsplit("@", 1)[-1].split(":")[0].lower().rstrip(".")
    if host.startswith("www."):
        host = host[len("www."):]
    return host or None


def _matches(host: str, domains) -> bool:
    """True if `host` is one of `domains` or a subdomain of one. Not a substring check."""
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def classify_host(host: str, own_domain: str) -> str:
    """
    The layer for `host` (already normalized): own_site first, then
    reference, review_media, community; anything else is other_companies.
    """
    if _matches(host, (own_domain,)):
        return "own_site"
    if REFERENCE_TLD_PATTERN.search(host) or _matches(host, REFERENCE_DOMAINS):
        return "reference"
    if _matches(host, REVIEW_MEDIA_DOMAINS):
        return "review_media"
    if _matches(host, COMMUNITY_DOMAINS) or host.startswith("community."):
        return "community"
    return "other_companies"


# ---------------------------------------------------------------------------
# Page types for other companies' pages (our judgment, versioned by
# PAGE_TYPE_RULES_VERSION). Applied to pages in the other_companies layer
# only. Pure rules over the URL and, when the page could be fetched, its
# HTML <title>.
# ---------------------------------------------------------------------------

# lists: the URL or the title contains one of these as a whole word.
LIST_KEYWORDS = ("best", "top", "vs", "versus", "alternatives", "comparison", "compare", "review", "reviews")

# guides: the URL's path (or a subdomain label, such as support.example.com)
# contains one of these as a whole word, and the page is not a list.
GUIDE_KEYWORDS = ("blog", "guide", "how-to", "learn", "docs", "help", "support", "knowledge", "academy", "resources")

_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
_LIST_RE = re.compile(r"(?<![a-z0-9])(?:" + "|".join(map(re.escape, LIST_KEYWORDS)) + r")(?![a-z0-9])")
# A guide keyword, optionally plural ("guide" / "guides", "blog" / "blogs").
_GUIDE_RE = re.compile(r"(?<![a-z0-9])(?:" + "|".join(map(re.escape, GUIDE_KEYWORDS)) + r")s?(?![a-z0-9])")


def _url_parts(url: str) -> tuple[list[str], str]:
    """(subdomain labels, lowercase path) of a URL; the last two host labels are taken as the site's own name."""
    host = normalize_host(url) or ""
    labels = host.split(".")
    subdomains = labels[:-2] if len(labels) > 2 else []
    parsed = urlparse(url if "//" in url else f"//{url}")
    return subdomains, parsed.path.lower()


def classify_page_type(url: str, title: str | None = None) -> str:
    """
    "lists", "guides" or "product_pages" for a page of another company.
    lists wins over guides; anything else is a product page. `title` is the
    page's HTML <title>, or None when it couldn't be fetched (then only the
    URL decides).
    """
    subdomains, path = _url_parts(url)
    url_words = _WORD_SPLIT.sub("-", path).strip("-")
    title_words = _WORD_SPLIT.sub("-", (title or "").lower()).strip("-")

    if _LIST_RE.search(url_words) or _LIST_RE.search(title_words):
        return "lists"
    guide_haystack = "-".join(["-".join(subdomains), url_words]).strip("-")
    if _GUIDE_RE.search(guide_haystack):
        return "guides"
    return "product_pages"


def classify_url(url: str, own_domain: str) -> str | None:
    """The layer for a citation URL, or None if the URL has no usable host."""
    host = normalize_host(url)
    return classify_host(host, normalize_host(own_domain) or "") if host else None
