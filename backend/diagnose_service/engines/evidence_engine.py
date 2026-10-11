"""
Evidence density engine, rule-based.

Scores how much concrete, checkable evidence a company's own website
shows, using ONLY deterministic code rules. There is no LLM call, no
randomness, and no cache anywhere in this module: the same page text
(and HTML) always produces exactly the same evidence list and score, on
any machine, at any time. Earlier versions asked an LLM to list and vote
on evidence; that was unstable on identical input (see
backend/scripts/stability_audit.py), so the judgment is now ours, as code.

Pages read: the homepage, plus at most one page of each of four kinds
found in the homepage's links (see _select_pages): customers/case
studies, security/trust, pricing, about. URLs are normalized and
deduplicated; a page that can't be found is simply not read. Body text is
extracted with method="full". Every evidence item records the page it
came from.

Four kinds of evidence, each found by explicit rules (see the find_*
functions):

  number_or_statistic              a number WITH a unit or symbol: 40%,
                                   $100,000, 11,500+, 2B+, 10x, a "4.7
                                   rating". Years, phone numbers, dates,
                                   version numbers, plan prices, and
                                   anything next to "ago" are excluded.
  attributed_quote                 a named customer testimonial: "Name,
                                   Title @ Company", "Name, Title at
                                   Company", "— Name, Company" after a
                                   quote, or an HTML block marked as a
                                   testimonial (blockquote, schema.org
                                   Review or Testimonial).
  named_customer_or_case_study     a company named in a customer logo
                                   area, next to "trusted by" style
                                   wording, or listed as a customer in
                                   schema.org. What the rules can't
                                   decide is not counted; nothing is
                                   guessed.
  third_party_source_or_           a name from THIRD_PARTY_TERMS (review
  certification                    sites, analysts, certifications).

The same piece of evidence appearing on several pages counts once (the
first page in reading order keeps it; the others are recorded as
also_found_on).

Score (OUR judgment, a fixed formula, not a measurement of anything
external): each category earns points from its evidence count with
_points_for_item_count, up to MAX_POINTS_PER_CATEGORY, four categories,
100 maximum. Finding no evidence is a correct, low score, not missing
data. The score is -1 only when no page could be fetched at all.
"""

import json
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from shared.config import settings
from shared._fetch_utils import extract_text_from_html
from engines._provenance import page_fingerprint
from models import DimensionScore

DIMENSION_NAME = "evidence_density"

# Bump whenever this dimension's measurement method changes in a way
# that makes results computed before and after not directly comparable.
METHOD_VERSION = "v3-rules"

EVIDENCE_CATEGORIES = [
    "number_or_statistic",
    "named_customer_or_case_study",
    "attributed_quote",
    "third_party_source_or_certification",
]

_CATEGORY_LABELS = {
    "number_or_statistic": "specific numbers or statistics",
    "named_customer_or_case_study": "named customers",
    "attributed_quote": "named customer testimonials",
    "third_party_source_or_certification": "third-party ratings or certifications",
}

# Our own scoring formula: each category is worth up to
# MAX_POINTS_PER_CATEGORY points by how many distinct pieces of evidence
# were found. 3+ earns full marks; fewer earns a smaller fixed amount.
# 4 categories x 25 points = 100 max.
MAX_POINTS_PER_CATEGORY = 25
_POINTS_BY_ITEM_COUNT = {0: 0, 1: 15, 2: 20}


def _points_for_item_count(count: int) -> int:
    """Our fixed rule mapping a category's evidence count to points. See MAX_POINTS_PER_CATEGORY."""
    if count >= 3:
        return MAX_POINTS_PER_CATEGORY
    return _POINTS_BY_ITEM_COUNT.get(count, 0)


# ---------------------------------------------------------------------------
# Which pages are read
# ---------------------------------------------------------------------------

# Page kinds beyond the homepage, in reading order, each with the words
# that identify it in a link's path or link text. At most one page per kind.
PAGE_KINDS = [
    ("customers", ["customers", "case-studies", "stories"]),
    ("security", ["security", "trust", "compliance"]),
    ("pricing", ["pricing"]),
    ("about", ["about"]),
]

_SKIPPED_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".zip", ".mp4", ".xml")


def _host_of(url: str) -> str:
    host = urlparse(url if "//" in url else f"//{url}").netloc.split("@")[-1].split(":")[0].lower()
    return host[4:] if host.startswith("www.") else host


def _normalize_url(url: str) -> str:
    """
    Canonical form used for deduplication and fetching: https, lowercase
    host without "www.", no query string, no fragment, no trailing slash.
    """
    if not url.startswith("http"):
        url = f"https://{url}"
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return f"https://{_host_of(url)}{path}"


def _is_same_site(url: str, base_host: str) -> bool:
    host = _host_of(url)
    return host == base_host or host.endswith("." + base_host)


def _select_pages(homepage_url: str, soup: BeautifulSoup) -> list[tuple[str, str]]:
    """
    [(kind, url)] for the key pages found in the homepage's links, at
    most one per kind in PAGE_KINDS order. Among a kind's candidates a
    match in the URL path beats a match only in the link text, then the
    shallower path wins, then the earlier link in the document. A URL is
    only ever used for one kind.
    """
    base_host = _host_of(homepage_url)
    home_key = _normalize_url(homepage_url)

    candidates = []
    for order, anchor in enumerate(soup.find_all("a", href=True)):
        href = urljoin(homepage_url, anchor["href"].strip())
        if not href.startswith("http") or urlparse(href).path.lower().endswith(_SKIPPED_EXTENSIONS):
            continue
        if not _is_same_site(href, base_host):
            continue
        key = _normalize_url(href)
        if key == home_key:
            continue
        candidates.append(
            {
                "url": key,
                "path": urlparse(key).path.lower(),
                "text": re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).lower(),
                "order": order,
            }
        )

    chosen: list[tuple[str, str]] = []
    used: set[str] = {home_key}
    for kind, keywords in PAGE_KINDS:
        ranked = []
        for candidate in candidates:
            if candidate["url"] in used:
                continue
            in_path = any(word in candidate["path"] for word in keywords)
            in_text = any(word in candidate["text"] for word in keywords)
            if not (in_path or in_text):
                continue
            depth = candidate["path"].count("/")
            ranked.append(((0 if in_path else 1, depth, candidate["order"]), candidate["url"]))
        if ranked:
            url = min(ranked)[1]
            chosen.append((kind, url))
            used.add(url)
    return chosen


# ---------------------------------------------------------------------------
# Evidence rules
# ---------------------------------------------------------------------------

def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _context(text: str, start: int, end: int, radius: int = 60) -> str:
    """The original wording around [start, end), widened by `radius` characters and cut at word boundaries."""
    left, right = max(0, start - radius), min(len(text), end + radius)
    snippet = text[left:right]
    if left > 0 and " " in snippet:
        snippet = snippet.split(" ", 1)[1]
    if right < len(text) and " " in snippet:
        snippet = snippet.rsplit(" ", 1)[0]
    return snippet.strip()


def _item(category: str, key: str, text: str, context: str, rule: str) -> dict:
    return {"category": category, "key": key, "text": text, "context": context, "rule": rule}


# --- a. specific numbers ----------------------------------------------------

# Text that is not a stat even though it contains digits. Each match is
# overwritten with spaces (same length, so positions stay valid) BEFORE
# the number rules run.
_MASKS = [
    # phone numbers
    re.compile(r"(?:\+?\d{1,2}[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"),
    # version numbers like 4.0.1 or 6.4.3
    re.compile(r"\bv?\d+\.\d+\.\d+(?:\.\d+)*\b"),
    # dates: 10/20/2026, October 20, 2026, 20 October 2026
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?(?:\s+(?:19|20)\d{2})?\b"
    ),
    re.compile(
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?(?:,?\s+(?:19|20)\d{2})?\b"
    ),
    # years, unless a unit follows (2000+ customers)
    re.compile(r"\b(?:19|20)\d{2}\b(?!\s?(?:\+|%|[kKmMbB]\b|x\b))"),
]

_COUNT_NOUNS = (
    "customers|users|teams|companies|businesses|organizations|merchants|developers|clients|downloads|"
    "transactions|calls|emails|texts|messages|integrations|countries|employees|stores|sites|websites|"
    "apps|reviews|projects|deals|leads|agents|firms|brands|members"
)

_LEFT = r"(?<![\w.,])"

# (rule name, pattern). Matches are collected from all rules, then
# overlaps are resolved by keeping the earliest, then the longest.
_NUMBER_RULES = [
    ("percent", re.compile(_LEFT + r"\d[\d,]*(?:\.\d+)?\s?%")),
    ("currency", re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:[KkMmBb]\b|million|billion|thousand))?\+?")),
    ("multiplier", re.compile(_LEFT + r"\d+(?:\.\d+)?[xX](?!\w)")),
    ("magnitude_plus", re.compile(_LEFT + r"\d+(?:\.\d+)?\s?[KMB]\+")),
    ("magnitude", re.compile(_LEFT + r"\d+(?:\.\d+)?[KMB]\b")),
    ("magnitude_word", re.compile(_LEFT + r"\d+(?:\.\d+)?\s(?:million|billion|thousand)\b", re.IGNORECASE)),
    ("count_plus", re.compile(r"(?<![\w.,+])\d[\d,]*\s?\+(?![\w+])")),
    ("counted_noun", re.compile(_LEFT + r"\d[\d,]*(?:\.\d+)?\+?\s(?:" + _COUNT_NOUNS + r")\b", re.IGNORECASE)),
    ("rating_word", re.compile(r"\b[1-5](?:\.\d)?\s*(?:[^\w\s]\s*)?(?:stars?|rated|ratings?)\b", re.IGNORECASE)),
    ("rating_out_of", re.compile(r"\b[1-5](?:\.\d)?\s*out of 5\b", re.IGNORECASE)),
    ("rating_slash", re.compile(r"\b[1-5]\.\d\s*/\s*5\b")),
    ("rated_number", re.compile(r"\brated\s+[1-5]\.\d\b", re.IGNORECASE)),
]

# A number within this window of any of these is product UI text (a mocked-up
# app on the page), not marketing evidence.
_UI_NOISE = re.compile(r"\bago\b|\bworked for\b|\bjust now\b", re.IGNORECASE)
_UI_NOISE_RADIUS = 30

# Currency that is a plan price rather than a customer result.
_PLAN_PRICE_TAIL = re.compile(
    r"^\s*(?:/\s?(?:mo|month|user|seat|yr|year)|per\s+(?:month|user|seat|year|mo|agent|member)|a month|monthly|annually|billed)",
    re.IGNORECASE,
)


def _mask(text: str) -> str:
    masked = text
    for pattern in _MASKS:
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    return masked


def find_numbers(text: str, page_kind: str = "home") -> list[dict]:
    """Specific numbers with a unit or symbol. See the module docstring for what is excluded."""
    masked = _mask(text)
    found = []
    for rule, pattern in _NUMBER_RULES:
        for match in pattern.finditer(masked):
            found.append((match.start(), -(match.end() - match.start()), match.end(), rule))
    found.sort()

    items, last_end = [], -1
    for start, _neg_length, end, rule in found:
        if start < last_end:
            continue  # overlaps a match we already kept
        window = text[max(0, start - _UI_NOISE_RADIUS) : end + _UI_NOISE_RADIUS]
        if _UI_NOISE.search(window):
            continue
        if rule == "currency":
            if page_kind == "pricing" or _PLAN_PRICE_TAIL.match(text[end : end + 24]):
                continue
        last_end = end
        token = _normalize_space(text[start:end])
        key = re.sub(r"[^\w%+$€£.]", "", token.lower())
        items.append(_item("number_or_statistic", key, token, _context(text, start, end), f"number:{rule}"))
    return items


# --- b. customer testimonials -----------------------------------------------

_ROLE_WORDS = {
    "ceo", "cto", "cfo", "coo", "cmo", "cro", "cio", "ciso", "founder", "co-founder", "cofounder", "president",
    "owner", "partner", "director", "head", "manager", "vp", "svp", "evp", "vice", "chief", "lead", "engineer",
    "officer", "principal", "attorney", "counsel", "consultant", "designer", "developer", "analyst",
    "administrator", "supervisor", "coordinator", "specialist", "producer", "recruiter", "agent", "realtor",
    "broker", "architect", "teacher", "professor", "dean", "superintendent", "controller", "advisor",
}

_NAME_TOKEN = re.compile(r"^(?:[A-Z][A-Za-z'’\-]+|[A-Z]\.)$")
_COMPANY = r"[A-Z0-9][\w&'’\-\.]*(?:\s+[A-Z0-9][\w&'’\-\.]*){0,3}"
_TITLE_COMPANY = re.compile(r",\s*(?P<title>[^,@]{2,60}?)\s*(?:@|\bat\b)\s*(?P<company>" + _COMPANY + r")")
_DASH_ATTRIBUTION = re.compile(
    r"[\"”’»]\s*[—–―-]+\s*(?P<name>(?:[A-Z][A-Za-z'’\-]+|[A-Z]\.)(?:\s+(?:[A-Z][A-Za-z'’\-]+|[A-Z]\.)){1,2})\s*,\s*(?P<company>"
    + _COMPANY
    + r")"
)


def _name_before(text: str, comma_pos: int) -> tuple[str, int] | None:
    """The 2-3 capitalized name tokens directly before `comma_pos`, as (name, start offset), or None."""
    prefix = text[:comma_pos].rstrip()
    position = len(prefix)
    tokens = []
    for token in reversed(prefix.split(" ")):
        if len(tokens) == 3 or not _NAME_TOKEN.match(token) or token.lower() in _ROLE_WORDS:
            break
        tokens.append(token)
        position -= len(token) + 1
    if len(tokens) < 2:
        return None
    tokens.reverse()
    return " ".join(tokens), position + 1


def _clean_company(company: str) -> str:
    first = company.split()[0].rstrip(".,;:")
    return first if "." in first[:-1] else " ".join(company.split()).rstrip(".,;:")


def find_attributions(text: str) -> list[dict]:
    """Testimonial attributions written in the page text: 'Name, Title @ Company', '... at Company', '— Name, Company'."""
    items, spans = [], []
    for match in _TITLE_COMPANY.finditer(text):
        title_words = {w.strip(".,&/").lower() for w in match.group("title").split()}
        if not (title_words & _ROLE_WORDS):
            continue
        name = _name_before(text, match.start())
        if not name:
            continue
        person, name_start = name
        company = _clean_company(match.group("company"))
        shown = f"{person}, {_normalize_space(match.group('title'))} @ {company}"
        items.append(
            _item("attributed_quote", re.sub(r"\W+", " ", person.lower()).strip(), shown,
                  _context(text, name_start, match.end(), radius=110), "attribution:name_title_company")
        )
        spans.append((name_start, match.end()))
    for match in _DASH_ATTRIBUTION.finditer(text):
        if any(start <= match.start("name") < end for start, end in spans):
            continue
        person = match.group("name")
        company = _clean_company(match.group("company"))
        items.append(
            _item("attributed_quote", re.sub(r"\W+", " ", person.lower()).strip(), f"{person}, {company}",
                  _context(text, match.start(), match.end(), radius=110), "attribution:dash_name_company")
        )
    return items


def _json_ld_objects(soup: BeautifulSoup):
    """Every dict found inside the page's JSON-LD blocks, at any depth, in document order."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop(0)
            if isinstance(node, dict):
                yield node
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                stack.extend(node)


def _types_of(node: dict) -> list[str]:
    raw = node.get("@type", [])
    return [raw] if isinstance(raw, str) else [t for t in raw if isinstance(t, str)]


def find_html_testimonials(soup: BeautifulSoup) -> list[dict]:
    """
    Blocks the HTML itself marks as customer reviews: <blockquote>,
    schema.org Review/Testimonial microdata, and JSON-LD Review/Testimonial
    objects. A block whose text already contains a written attribution is
    left to find_attributions, so one testimonial is not counted twice.
    """
    items = []

    def _add(text: str, rule: str):
        text = _normalize_space(text)
        if len(text) < 20 or find_attributions(text):
            return
        items.append(_item("attributed_quote", re.sub(r"\W+", " ", text.lower()).strip()[:200], text[:300], text[:300], rule))

    for block in soup.find_all("blockquote"):
        _add(block.get_text(" ", strip=True), "attribution:blockquote")
    for block in soup.find_all(attrs={"itemtype": re.compile(r"schema\.org/(Review|Testimonial)", re.IGNORECASE)}):
        _add(block.get_text(" ", strip=True), "attribution:schema_microdata")
    for node in _json_ld_objects(soup):
        if {"Review", "Testimonial"} & set(_types_of(node)):
            body = node.get("reviewBody") or node.get("text") or node.get("description") or ""
            author = node.get("author")
            author = author.get("name") if isinstance(author, dict) else author
            if isinstance(body, str) and body.strip():
                _add(f"{body} {author or ''}", "attribution:schema_jsonld")
    return items


# --- c. named customers -------------------------------------------------------

_TRUST_PHRASE = re.compile(
    r"trusted by|used by|loved by|relied on by|chosen by|customers include|teams at|companies like|brands like|used at",
    re.IGNORECASE,
)
_CUSTOMER_NAME = r"[A-Z][\w&'’\-\.]*(?:\s[A-Z][\w&'’\-\.]*){0,2}"
_TRUST_LIST = re.compile(
    r"(?i:trusted by|used by|loved by|relied on by|chosen by|customers include|teams at|companies like|brands like|used at)\s+"
    r"(?P<list>" + _CUSTOMER_NAME + r"(?:\s*(?:,\s*and|,|\band\b|&)\s*" + _CUSTOMER_NAME + r"){0,11})"
)
_LIST_SPLIT = re.compile(r"\s*(?:,\s*and|,|\band\b|&)\s*")
# Capitalized words that are not company names.
_NOT_COMPANIES = {"the", "our", "we", "you", "your", "their", "it", "this", "that", "more", "all", "many", "teams", "customers", "companies"}
_LOGO_AREA = re.compile(
    r"customer|client|trusted|logo[-_ ]?(?:wall|cloud|grid|strip|bar|carousel|row|list)|logos?[-_ ]?(?:wall|cloud|grid|strip|bar)",
    re.IGNORECASE,
)
_GENERIC_ALT = {"logo", "image", "icon", "arrow", "customer logo", "company logo", "client logo", "photo", "avatar"}


def _company_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def find_named_customers_in_text(text: str, own_label: str = "") -> list[dict]:
    """Companies listed right after 'trusted by' style wording, e.g. 'Security teams at Instacart, Reddit, and Gusto'."""
    items = []
    for match in _TRUST_LIST.finditer(text):
        for name in _LIST_SPLIT.split(match.group("list")):
            name = name.strip(" .,;:")
            if not name or name.split()[0].lower() in _NOT_COMPANIES or _company_key(name) in (own_label, ""):
                continue
            items.append(
                _item("named_customer_or_case_study", _company_key(name), name,
                      _context(text, match.start(), match.end(), radius=40), "customer:trusted_by_text")
            )
    return items


def find_named_customers_in_html(soup: BeautifulSoup, own_label: str = "") -> list[dict]:
    """
    Logo alt texts inside a customer logo area (an element whose class or id
    says customers/clients/trusted/logo-wall..., or whose own text opens with
    'trusted by' style wording), plus customers listed in JSON-LD. Logos in the
    page header or nav are ignored: those are the site's own brand.
    """
    items = []
    for image in soup.find_all("img"):
        alt = _normalize_space(image.get("alt") or "")
        alt = _normalize_space(re.sub(r"\b(?:logo|logotype|icon)\b", "", alt, flags=re.IGNORECASE)).strip(" -–—|:")
        if not (2 <= len(alt) <= 40) or not re.search(r"[A-Za-z]", alt) or alt.lower() in _GENERIC_ALT:
            continue
        if _company_key(alt) == own_label or image.find_parent(["header", "nav"]):
            continue
        for depth, ancestor in enumerate(image.parents):
            if depth >= 4 or ancestor.name in ("body", "html", "[document]"):
                break
            marker = f"{' '.join(ancestor.get('class') or [])} {ancestor.get('id') or ''}"
            local_text = ancestor.get_text(" ", strip=True)
            if _LOGO_AREA.search(marker) or (len(local_text) < 1500 and _TRUST_PHRASE.search(local_text[:120])):
                items.append(_item("named_customer_or_case_study", _company_key(alt), alt, f"logo: {alt}", "customer:logo_area"))
                break
    for node in _json_ld_objects(soup):
        for field in ("customer", "customers", "client", "clients"):
            value = node.get(field)
            for entry in value if isinstance(value, list) else [value]:
                name = entry.get("name") if isinstance(entry, dict) else entry if isinstance(entry, str) else None
                if name and _company_key(name) not in (own_label, ""):
                    items.append(_item("named_customer_or_case_study", _company_key(name), name, f"schema.org {field}: {name}", "customer:schema_org"))
    return items


# --- d. third-party ratings and certifications ---------------------------------

# Fixed vocabulary of third-party names. Add a (label, regex) pair here to
# teach the engine a new one. Matching is case-insensitive except G2.
THIRD_PARTY_TERMS = [
    ("G2", re.compile(r"\bG2\b")),
    ("Capterra", re.compile(r"\bCapterra\b", re.IGNORECASE)),
    ("Gartner", re.compile(r"\bGartner\b", re.IGNORECASE)),
    ("Forrester", re.compile(r"\bForrester\b", re.IGNORECASE)),
    ("SOC 2", re.compile(r"\bSOC\s?2\b", re.IGNORECASE)),
    ("ISO 27001", re.compile(r"\bISO\s?/?(?:IEC\s)?27001\b", re.IGNORECASE)),
    ("HIPAA", re.compile(r"\bHIPAA\b", re.IGNORECASE)),
    ("GDPR", re.compile(r"\bGDPR\b", re.IGNORECASE)),
    ("PCI DSS", re.compile(r"\bPCI[\s-]?DSS\b", re.IGNORECASE)),
    ("Trustpilot", re.compile(r"\bTrustpilot\b", re.IGNORECASE)),
    ("TrustRadius", re.compile(r"\bTrustRadius\b", re.IGNORECASE)),
    ("GetApp", re.compile(r"\bGetApp\b", re.IGNORECASE)),
    ("Software Advice", re.compile(r"\bSoftware Advice\b", re.IGNORECASE)),
    ("FedRAMP", re.compile(r"\bFedRAMP\b", re.IGNORECASE)),
]


def find_third_party(text: str) -> list[dict]:
    """One item per THIRD_PARTY_TERMS name found (its first occurrence on the page)."""
    items = []
    for label, pattern in THIRD_PARTY_TERMS:
        match = pattern.search(text)
        if match:
            items.append(
                _item("third_party_source_or_certification", label.lower(), label,
                      _context(text, match.start(), match.end()), "third_party:term_list")
            )
    return items


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------

def extract_page_evidence(url: str, kind: str, html: str, own_label: str = "") -> tuple[str, list[dict]]:
    """
    (page text, evidence items) for one page. Items come in a fixed order:
    by category in EVIDENCE_CATEGORIES order, then by the order the rules
    found them in the page. Each carries its source page.
    """
    text = extract_text_from_html(html, method="full")
    soup = BeautifulSoup(html, "html.parser")

    by_category = {
        "number_or_statistic": find_numbers(text, kind),
        "named_customer_or_case_study": find_named_customers_in_text(text, own_label)
        + find_named_customers_in_html(soup, own_label),
        "attributed_quote": find_attributions(text) + find_html_testimonials(soup),
        "third_party_source_or_certification": find_third_party(text),
    }
    items = []
    for category in EVIDENCE_CATEGORIES:
        for item in by_category[category]:
            items.append({**item, "source_url": url, "source_page": kind})
    return text, items


def merge_evidence(per_page_items: list[list[dict]]) -> list[dict]:
    """
    Merge items from pages given in reading order. The same (category, key)
    on several pages is one piece of evidence: the first page keeps it and
    the others are listed in also_found_on.
    """
    merged: dict[tuple[str, str], dict] = {}
    for items in per_page_items:
        for item in items:
            identity = (item["category"], item["key"])
            if identity in merged:
                other = merged[identity]
                if item["source_url"] != other["source_url"] and item["source_url"] not in other["also_found_on"]:
                    other["also_found_on"].append(item["source_url"])
                continue
            merged[identity] = {**item, "also_found_on": []}
    return sorted(merged.values(), key=lambda i: EVIDENCE_CATEGORIES.index(i["category"]))


def _score_from_items(items: list[dict]) -> tuple[int, dict, dict]:
    """(score, counts_by_category, points_by_category) from the merged evidence list."""
    counts = {category: 0 for category in EVIDENCE_CATEGORIES}
    for item in items:
        counts[item["category"]] += 1
    points = {category: _points_for_item_count(count) for category, count in counts.items()}
    return sum(points.values()), counts, points


def _fetch_html(url: str) -> tuple[str, str]:
    """(final URL, html) for `url`. Raises requests.RequestException on failure."""
    full_url = url if url.startswith("http") else f"https://{url}"
    response = requests.get(full_url, headers={"User-Agent": settings.http_user_agent}, timeout=settings.request_timeout_seconds)
    response.raise_for_status()
    return response.url, response.text


def _build_plain_finding(counts: dict, pages_read: list[dict]) -> dict:
    """Deterministic founder-readable summary built only from this run's counts. No AI writing."""
    parts = [f"{counts[c]} {_CATEGORY_LABELS[c]}" for c in EVIDENCE_CATEGORIES]
    found = (
        f"We read {len(pages_read)} page{'s' if len(pages_read) != 1 else ''} on your site and found "
        f"{parts[0]}, {parts[1]}, {parts[2]}, and {parts[3]}."
    )
    weakest = min(EVIDENCE_CATEGORIES, key=lambda c: (counts[c], EVIDENCE_CATEGORIES.index(c)))
    total = sum(counts.values())
    if total == 0:
        cost = "A customer or an AI search tool looking for proof on your site finds none, so your claims are taken on trust."
    elif counts[weakest] >= 3:
        cost = "Your site backs its claims with proof in every area we check."
    else:
        cost = f"Your site is thin on {_CATEGORY_LABELS[weakest]}, so that part of your story rests on claims alone."
    fix = (
        "Keep adding specific proof next to your biggest claims."
        if counts[weakest] >= 3
        else f"Add real {_CATEGORY_LABELS[weakest]} next to your biggest claim on the homepage."
    )
    return {"what_we_found": found, "what_it_costs_you": cost, "fix_it_today": fix}


def check_evidence_density(domain: str) -> DimensionScore:
    """
    Score how much concrete evidence `domain`'s website shows, by the
    rules in this module's docstring. No LLM, no cache: same pages in,
    same score out.

    Returns score == -1 only when not a single page could be fetched.
    A site with no evidence found scores 0 with status "ok".
    """
    own_label = _company_key(_host_of(_normalize_url(domain)).split(".")[0])

    try:
        homepage_url, homepage_html = _fetch_html(domain)
    except requests.RequestException as exc:
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": f"Failed to fetch homepage for '{domain}': {exc}",
                "pages_failed": [{"url": domain, "kind": "home", "error": str(exc)}],
                "plain_finding": {
                    "what_we_found": f"We couldn't open {domain}, so we have no evidence to count.",
                    "what_it_costs_you": "",
                    "fix_it_today": "Check that the site loads for a visitor, then run the report again.",
                },
            },
        )

    homepage_url = _normalize_url(homepage_url)
    pages_to_read = [("home", homepage_url, homepage_html)]
    pages_failed = []
    for kind, url in _select_pages(homepage_url, BeautifulSoup(homepage_html, "html.parser")):
        try:
            final_url, html = _fetch_html(url)
        except requests.RequestException as exc:
            pages_failed.append({"url": url, "kind": kind, "error": str(exc)})
            continue
        pages_to_read.append((kind, _normalize_url(final_url), html))

    pages_read, fingerprints, per_page_items = [], [], []
    for kind, url, html in pages_to_read:
        text, items = extract_page_evidence(url, kind, html, own_label)
        per_page_items.append(items)
        pages_read.append({"kind": kind, "url": url, "text_length": len(text), "evidence_found": len(items)})
        fingerprints.append(page_fingerprint(url, text))

    items = merge_evidence(per_page_items)
    score, counts, points = _score_from_items(items)

    keys_by_page: dict[str, dict[str, set]] = {c: {} for c in EVIDENCE_CATEGORIES}
    for page_items, page in zip(per_page_items, pages_read):
        for item in page_items:
            keys_by_page[item["category"]].setdefault(page["kind"], set()).add(item["key"])
    pages_by_category = {
        c: {kind: len(keys) for kind, keys in keys_by_page[c].items()} for c in EVIDENCE_CATEGORIES
    }

    return DimensionScore(
        dimension=DIMENSION_NAME,
        score=score,
        details={
            "status": "ok",
            "items": items,
            "counts_by_category": counts,
            "points_by_category": points,
            "pages_by_category": pages_by_category,
            "scoring_rule": (
                "Our own fixed formula, not an AI judgment and not an outside standard: each of the 4 evidence "
                f"categories is worth up to {MAX_POINTS_PER_CATEGORY} points by how many distinct pieces of "
                f"evidence the code rules found ({_POINTS_BY_ITEM_COUNT}, {MAX_POINTS_PER_CATEGORY} points for 3 "
                "or more). Finding none is a score of 0, not missing data."
            ),
            "pages_read": pages_read,
            "pages_failed": pages_failed,
            "page_fingerprints": fingerprints,
            "plain_finding": _build_plain_finding(counts, pages_read),
        },
    )
