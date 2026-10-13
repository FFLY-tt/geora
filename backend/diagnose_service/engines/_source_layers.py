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

# Bump whenever any list or rule below changes.
LAYER_LIST_VERSION = "v1"

# Fixed order used to break ties (see source_layers.largest_gap_layer) and
# to order the layers in the report.
LAYER_ORDER = ["reference", "review_media", "community", "own_site", "other_companies"]

# reference: encyclopedias, standards bodies, industry bodies. Government
# (.gov) and university (.edu) sites are matched by REFERENCE_TLD_PATTERN.
REFERENCE_DOMAINS = (
    "acm.org",
    "aicpa.org",
    "britannica.com",
    "cloudsecurityalliance.org",
    "europa.eu",
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
    "arstechnica.com",
    "bleepingcomputer.com",
    "businessinsider.com",
    "capterra.com",
    "cnet.com",
    "computerworld.com",
    "crn.com",
    "csoonline.com",
    "darkreading.com",
    "entrepreneur.com",
    "fastcompany.com",
    "forbes.com",
    "forrester.com",
    "gartner.com",
    "getapp.com",
    "g2.com",
    "idc.com",
    "inc.com",
    "infoworld.com",
    "nerdwallet.com",
    "pcmag.com",
    "peerspot.com",
    "producthunt.com",
    "securityweek.com",
    "siliconangle.com",
    "softwareadvice.com",
    "sourceforge.net",
    "techcrunch.com",
    "techradar.com",
    "techtarget.com",
    "theregister.com",
    "thehackernews.com",
    "theverge.com",
    "trustpilot.com",
    "trustradius.com",
    "venturebeat.com",
    "wired.com",
    "zdnet.com",
)

# community: places where people talk, ask, and post.
COMMUNITY_DOMAINS = (
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
    if _matches(host, COMMUNITY_DOMAINS):
        return "community"
    return "other_companies"


def classify_url(url: str, own_domain: str) -> str | None:
    """The layer for a citation URL, or None if the URL has no usable host."""
    host = normalize_host(url)
    return classify_host(host, normalize_host(own_domain) or "") if host else None
