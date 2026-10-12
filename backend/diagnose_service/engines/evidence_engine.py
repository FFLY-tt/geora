"""
Evidence density engine, rule-based.

Scores how much concrete, checkable evidence a company's own website
shows, using ONLY deterministic code rules. There is no LLM call, no
randomness, and no cache anywhere in this module: the same page text
(and HTML) always produces exactly the same evidence list and score, on
any machine, at any time. Earlier versions asked an LLM to list and vote
on evidence; that was unstable on identical input (see
backend/scripts/stability_audit.py), so the judgment is now ours, as code.

Precision over recall: a scoring tool that counts something wrong inflates
the score and misleads the user, while a miss only makes the score
conservative. When a rule can't tell, the item is not counted. Known
misses are listed in docs/evidence_rules_known_limits.md.

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
                                   version numbers, anything next to
                                   "ago", anything on the pricing page,
                                   any value of 0, and any sentence
                                   containing a word from
                                   EXCLUDED_NUMBER_SENTENCE_WORDS are
                                   excluded. Deduplicated by value +
                                   unit, so "11,500+" and "11,500+ teams"
                                   are one item.
  attributed_quote                 a testimonial WITH an attribution (a
                                   person's name, or a title + company):
                                   "Name, Title @ Company", "Name, Title
                                   at Company", "— Name, Company" after a
                                   quote, or an HTML review block
                                   (blockquote, schema.org Review or
                                   Testimonial) that carries such an
                                   attribution. One person counts once,
                                   however their title is written.
  customer_stories                 distinct customer story pages linked
                                   one level below the customers page
                                   (/customers/acme under /customers).
                                   See find_customer_stories for why
                                   this replaced customer logos.
  third_party_source_or_           a name from THIRD_PARTY_TERMS. Review
  certification                    sites and analysts count anywhere;
                                   compliance standards only on the
                                   security page or in a sentence that
                                   says the company is certified,
                                   audited, compliant, and so on. Never
                                   inside a question.

The same piece of evidence appearing on several pages counts once (the
first page in reading order keeps it; the others are recorded as
also_found_on).

Score (OUR judgment, a fixed formula, not a measurement of anything
external): each category earns points from its evidence count with
_points_for_item_count, up to MAX_POINTS_PER_CATEGORY, four categories,
100 maximum. Finding no evidence is a correct, low score, not missing
data. The score is -1 only when the homepage can't be fetched, and
details.fetch_failure then says why (for example "blocked: HTTP 403").
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
METHOD_VERSION = "v3.2-rules"

EVIDENCE_CATEGORIES = [
    "number_or_statistic",
    "customer_stories",
    "attributed_quote",
    "third_party_source_or_certification",
]

_CATEGORY_LABELS = {
    "number_or_statistic": "specific numbers or statistics",
    "customer_stories": "customer story pages",
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
# that identify it in a link's path. At most one page per kind.
PAGE_KINDS = [
    ("customers", ["customers", "case-studies", "stories"]),
    ("security", ["security", "trust", "compliance"]),
    ("pricing", ["pricing"]),
    ("about", ["about"]),
]

# Subdomains that are a security page in their own right (trust.example.com).
_SECURITY_SUBDOMAINS = ("trust.", "security.")

# Named customers are only looked for on these page kinds.
_CUSTOMER_PAGE_KINDS = ("home", "customers")

_INDEX_FILES = ("index.html", "index.htm", "index.php")

_SKIPPED_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".zip", ".mp4", ".xml")


def _full_host(url: str) -> str:
    return urlparse(url if "//" in url else f"//{url}").netloc.split("@")[-1].split(":")[0].lower()


def _host_of(url: str) -> str:
    host = _full_host(url)
    return host[4:] if host.startswith("www.") else host


def _normalize_url(url: str) -> str:
    """
    Canonical form used for deduplication and fetching: https, lowercase
    host without "www.", no query string, no fragment, no trailing slash,
    and no trailing index file (/index.html is the same page as /).
    """
    if not url.startswith("http"):
        url = f"https://{url}"
    path = urlparse(url).path
    segments = path.split("/")
    if segments and segments[-1].lower() in _INDEX_FILES:
        path = "/".join(segments[:-1])
    return f"https://{_host_of(url)}{path.rstrip('/')}"


def _is_same_site(url: str, base_host: str) -> bool:
    host = _host_of(url)
    return host == base_host or host.endswith("." + base_host)


def _select_pages(homepage_url: str, soup: BeautifulSoup) -> list[tuple[str, str]]:
    """
    [(kind, url)] for the key pages found in the homepage's links, at
    most one per kind in PAGE_KINDS order. For each kind, in order:

    1. a link whose FIRST path segment equals one of the kind's words
       (/security, /customers, ...), or, for the security kind, a
       trust. / security. subdomain;
    2. only if there is none, a link whose path contains one of the words.

    Within a tier the shortest path wins, then the earlier link in the
    document. A URL is only ever used for one kind.
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
        path = urlparse(key).path.lower()
        candidates.append(
            {
                "url": key,
                "path": path,
                "first_segment": path.strip("/").split("/")[0],
                "host": _full_host(key),
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
            exact = candidate["first_segment"] in keywords or (
                kind == "security" and candidate["host"].startswith(_SECURITY_SUBDOMAINS)
            )
            contains = any(word in candidate["path"] for word in keywords)
            if not (exact or contains):
                continue
            ranked.append(((0 if exact else 1, len(candidate["path"]), candidate["order"]), candidate["url"]))
        if ranked:
            url = min(ranked)[1]
            chosen.append((kind, url))
            used.add(url)
    return chosen


# ---------------------------------------------------------------------------
# Shared text helpers
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


# Sentence boundaries in whitespace-collapsed page text: after . ! ? before
# a space, and at quote marks, bullets, and separators, which on real pages
# usually stand where a line break was.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|[“”•|·]")


def _sentence_at(text: str, start: int, end: int) -> str:
    """The sentence of `text` that contains [start, end)."""
    left, right = 0, len(text)
    for match in _SENTENCE_BREAK.finditer(text):
        if match.end() <= start:
            left = match.end()
        elif match.start() >= end:
            right = match.start()
            break
    return text[left:right]


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

# (rule name, unit, pattern). Matches are collected from all rules, then
# overlaps are resolved by keeping the earliest, then the longest. The
# unit is what the dedup key is built from (see _number_key).
_NUMBER_RULES = [
    ("percent", "percent", re.compile(_LEFT + r"\d[\d,]*(?:\.\d+)?\s?%")),
    ("currency", "currency", re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:[KkMmBb]\b|million|billion|thousand))?\+?")),
    ("multiplier", "multiplier", re.compile(_LEFT + r"\d+(?:\.\d+)?[xX](?!\w)")),
    ("magnitude_plus", "count", re.compile(_LEFT + r"\d+(?:\.\d+)?\s?[KMB]\+")),
    ("magnitude", "count", re.compile(_LEFT + r"\d+(?:\.\d+)?[KMB]\b")),
    ("magnitude_word", "count", re.compile(_LEFT + r"\d+(?:\.\d+)?\s(?:million|billion|thousand)\b", re.IGNORECASE)),
    ("count_plus", "count", re.compile(r"(?<![\w.,+])\d[\d,]*\s?\+(?![\w+])")),
    ("counted_noun", "count", re.compile(_LEFT + r"\d[\d,]*(?:\.\d+)?\+?\s(?:" + _COUNT_NOUNS + r")\b", re.IGNORECASE)),
    ("rating_word", "rating", re.compile(r"\b[1-5](?:\.\d)?\s*(?:[^\w\s]\s*)?(?:stars?|rated|ratings?)\b", re.IGNORECASE)),
    ("rating_out_of", "rating", re.compile(r"\b[1-5](?:\.\d)?\s*out of 5\b", re.IGNORECASE)),
    ("rating_slash", "rating", re.compile(r"\b[1-5]\.\d\s*/\s*5\b")),
    ("rated_number", "rating", re.compile(r"\brated\s+[1-5]\.\d\b", re.IGNORECASE)),
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

# A number in a sentence containing any of these words is about employee
# benefits, fines, fees, or offers, not proof of results. Add a word here
# to exclude more. Plurals (s / es / y->ies) are matched automatically.
EXCLUDED_NUMBER_SENTENCE_WORDS = [
    "401(k)", "insurance", "premium", "PTO", "vacation", "salary", "benefits", "penalty", "fine",
    "per violation", "fee", "discount", "off", "trial",
    # company facts, not customer results
    "grant", "funding", "raised", "Series A", "Series B", "Series C", "Series D", "Series E", "Series F",
    "valuation", "founded", "years of experience", "available in",
]


def _word_pattern(word: str) -> str:
    if word == "401(k)":
        return r"\b401\s?\(?k\)?"
    stem = re.escape(word)
    if word.endswith("y"):
        stem = re.escape(word[:-1]) + r"(?:y|ies)"
    else:
        stem += r"(?:s|es)?"
    return r"\b" + stem + r"\b"


_EXCLUDED_SENTENCE_RE = re.compile("|".join(_word_pattern(w) for w in EXCLUDED_NUMBER_SENTENCE_WORDS), re.IGNORECASE)

_MULTIPLIERS = {"k": 1e3, "m": 1e6, "b": 1e9, "thousand": 1e3, "million": 1e6, "billion": 1e9}
_FIRST_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SUFFIX = re.compile(r"\d\s?(thousand|million|billion|[kmb])(?![a-z])", re.IGNORECASE)


def _number_value(unit: str, token: str) -> float:
    value = float(_FIRST_NUMBER.search(token).group(0).replace(",", ""))
    if unit in ("count", "currency"):
        suffix = _SUFFIX.search(token)
        if suffix:
            value *= _MULTIPLIERS[suffix.group(1).lower()]
    return value


def _number_key(unit: str, token: str) -> str:
    """
    Dedup key from value + unit, so different wordings of one stat match:
    "11,500+", "11,500 +", "11,500+ teams" -> count:11500; "4.7 rating",
    "Rated 4.7", "4.7 / 5" -> rating:4.7; "$100K" and "$100,000" -> currency:$100000.
    """
    number = format(_number_value(unit, token), ".6g")
    if unit == "currency":
        return f"currency:{token.strip()[0]}{number}"
    return f"{unit}:{number}"


def _mask(text: str) -> str:
    masked = text
    for pattern in _MASKS:
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    return masked


def find_numbers(text: str, page_kind: str = "home") -> list[dict]:
    """Specific numbers with a unit or symbol. See the module docstring for what is excluded."""
    if page_kind == "pricing":
        return []  # plan limits and prices are product features, not proof
    masked = _mask(text)
    found = []
    for rule, unit, pattern in _NUMBER_RULES:
        for match in pattern.finditer(masked):
            found.append((match.start(), -(match.end() - match.start()), match.end(), rule, unit))
    found.sort()

    items, last_end = [], -1
    for start, _neg_length, end, rule, unit in found:
        if start < last_end:
            continue  # overlaps a match we already kept
        last_end = end
        window = text[max(0, start - _UI_NOISE_RADIUS) : end + _UI_NOISE_RADIUS]
        if _UI_NOISE.search(window):
            continue
        if rule == "currency" and _PLAN_PRICE_TAIL.match(text[end : end + 24]):
            continue
        if _EXCLUDED_SENTENCE_RE.search(_sentence_at(text, start, end)):
            continue
        token = _normalize_space(text[start:end])
        if _number_value(unit, token) == 0:
            continue  # "0%" is almost always an animated counter's starting value
        items.append(_item("number_or_statistic", _number_key(unit, token), token, _context(text, start, end), f"number:{rule}"))
    return items


# --- b. customer testimonials -----------------------------------------------

_ROLE_WORDS = {
    "ceo", "cto", "cfo", "coo", "cmo", "cro", "cio", "ciso", "founder", "co-founder", "cofounder", "president",
    "owner", "partner", "director", "head", "manager", "vp", "svp", "evp", "vice", "chief", "lead", "engineer",
    "officer", "principal", "attorney", "counsel", "consultant", "designer", "developer", "analyst",
    "administrator", "supervisor", "coordinator", "specialist", "producer", "recruiter", "agent", "realtor",
    "broker", "architect", "teacher", "professor", "dean", "superintendent", "controller", "advisor",
}

# Words that start a job title rather than continue a name ("Jem Millett
# Senior Director ..."). A name stops at the first of these or a role word.
_TITLE_QUALIFIERS = {
    "senior", "sr", "junior", "jr", "former", "ex", "associate", "assistant", "executive", "staff", "group",
    "regional", "global", "interim", "acting", "deputy", "general", "managing",
}

_NAME_TOKEN_RE = r"(?:[A-Z][A-Za-z'’\-]+|[A-Z]\.)"
_NAME_TOKEN = re.compile(r"^" + _NAME_TOKEN_RE + r"$")


def _is_title_word(token: str) -> bool:
    word = token.strip(".,").lower()
    return word in _ROLE_WORDS or word in _TITLE_QUALIFIERS


def _leading_person_name(caption: str) -> str | None:
    """The 2-3 capitalized name tokens a caption starts with, stopping at a title word; None if fewer than 2."""
    tokens = []
    for token in caption.lstrip(" —–―-").split():
        token = token.rstrip(",")
        if len(tokens) == 3 or not _NAME_TOKEN.match(token) or _is_title_word(token):
            break
        tokens.append(token)
    return " ".join(tokens) if len(tokens) >= 2 else None
_COMPANY = r"[A-Z0-9][\w&'’\-\.]*(?:\s+[A-Z0-9][\w&'’\-\.]*){0,3}"
_TITLE_COMPANY = re.compile(r",\s*(?P<title>[^,@]{2,60}?)\s*(?:@|\bat\b)\s*(?P<company>" + _COMPANY + r")")
_DASH_ATTRIBUTION = re.compile(
    r"[\"”’»]\s*[—–―-]+\s*(?P<name>" + _NAME_TOKEN_RE + r"(?:\s+" + _NAME_TOKEN_RE + r"){1,2})\s*,\s*(?P<company>"
    + _COMPANY
    + r")"
)


def _person_key(name: str) -> str:
    return re.sub(r"\W+", " ", name.lower()).strip()


def _name_before(text: str, comma_pos: int) -> tuple[str, int] | None:
    """The 2-3 capitalized name tokens directly before `comma_pos`, as (name, start offset), or None."""
    prefix = text[:comma_pos].rstrip()
    position = len(prefix)
    tokens = []
    for token in reversed(prefix.split(" ")):
        if len(tokens) == 3 or not _NAME_TOKEN.match(token) or _is_title_word(token):
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
            _item("attributed_quote", _person_key(person), shown,
                  _context(text, name_start, match.end(), radius=110), "attribution:name_title_company")
        )
        spans.append((name_start, match.end()))
    for match in _DASH_ATTRIBUTION.finditer(text):
        if any(start <= match.start("name") < end for start, end in spans):
            continue
        person = _leading_person_name(match.group("name"))
        if not person:
            continue
        company = _clean_company(match.group("company"))
        items.append(
            _item("attributed_quote", _person_key(person), f"{person}, {company}",
                  _context(text, match.start(), match.end(), radius=110), "attribution:dash_name_company")
        )
    return items


def _caption_attribution(caption: str) -> tuple[str, str] | None:
    """
    (key, shown) when a review block's caption (cite, footer, figcaption,
    schema author) is a real attribution: a person's name, or a title plus
    company. None otherwise; the block then does not count.
    """
    caption = _normalize_space(caption)
    if not caption:
        return None
    written = find_attributions(f"” — {caption}") or find_attributions(caption)
    if written:
        return written[0]["key"], written[0]["text"]
    name = _leading_person_name(caption)
    if name:
        return _person_key(name), caption[:120]
    words = {w.strip(".,&/").lower() for w in caption.split()}
    if words & _ROLE_WORDS and re.search(r"(?:@|\bat\b|,)\s*[A-Z0-9]", caption):
        return _person_key(caption), caption[:120]
    return None


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


def _block_caption(block) -> str:
    """Attribution text attached to an HTML review block: <cite>/<footer> inside it, or the <figcaption> of its <figure>."""
    parts = [tag.get_text(" ", strip=True) for tag in block.find_all(["cite", "footer"])]
    figure = block.find_parent("figure")
    if figure and figure.find("figcaption"):
        parts.append(figure.find("figcaption").get_text(" ", strip=True))
    author = block.find(attrs={"itemprop": "author"})
    if author:
        parts.append(author.get_text(" ", strip=True))
    return " ".join(p for p in parts if p)


def find_html_testimonials(soup: BeautifulSoup) -> list[dict]:
    """
    HTML review blocks (blockquote, schema.org Review/Testimonial microdata,
    JSON-LD Review/Testimonial) that carry an attribution. A block without
    one does not count. The item key is the person, so a testimonial also
    matched by find_attributions is merged into one item.
    """
    items = []

    def _add(quote: str, caption: str, rule: str):
        quote = _normalize_space(quote)
        attribution = find_attributions(quote)
        if attribution:
            key, shown = attribution[0]["key"], attribution[0]["text"]
        else:
            found = _caption_attribution(caption)
            if not found:
                return
            key, shown = found
        items.append(_item("attributed_quote", key, shown, quote[:300], rule))

    for block in soup.find_all("blockquote"):
        _add(block.get_text(" ", strip=True), _block_caption(block), "attribution:blockquote")
    for block in soup.find_all(attrs={"itemtype": re.compile(r"schema\.org/(Review|Testimonial)", re.IGNORECASE)}):
        _add(block.get_text(" ", strip=True), _block_caption(block), "attribution:schema_microdata")
    for node in _json_ld_objects(soup):
        if {"Review", "Testimonial"} & set(_types_of(node)):
            body = node.get("reviewBody") or node.get("text") or node.get("description") or ""
            author = node.get("author")
            author = author.get("name") if isinstance(author, dict) else author
            if isinstance(body, str) and body.strip() and isinstance(author, str):
                _add(body, author, "attribution:schema_jsonld")
    return items


# --- c. customer stories --------------------------------------------------------

# Why customer story pages and not customer logos (OUR judgment): an AI
# assistant reading a site cannot see logo images, so a logo is not
# evidence the AI can quote. Logo alt text also cannot tell a customer
# logo from an avatar, an illustration, or an icon. A separate customer
# story page is text the AI can read, cite, and link to. So this category
# counts the distinct story pages linked one level below the customers
# page, and logos are only listed for reference, never scored.
CUSTOMER_STORIES_REASON = (
    "Our judgment: AI assistants cannot see logo images, so logos are not evidence an AI can quote. "
    "A separate customer story page is text an AI can read and cite, so this category counts the "
    "distinct story pages linked one level below the customers page. Logos are listed for reference only."
)

# Query parameters that make a link a listing view (pagination, filters),
# not a story.
_LISTING_QUERY = re.compile(r"(?:^|&)(?:page|filter)\b", re.IGNORECASE)


def find_customer_stories(soup: BeautifulSoup, page_url: str) -> list[dict]:
    """
    Distinct links on the customers page (page_url, e.g. /customers) to a
    page exactly one level below it (/customers/acme), on the same host.
    URLs are normalized and deduplicated; pagination or filter links
    (?page=, ?filter=) and anchors back to the page itself (#top) are not
    stories. Each item records the link URL and its link text.
    """
    base = _normalize_url(page_url)
    base_path = urlparse(base).path.rstrip("/")
    base_host = _full_host(base)
    items, seen = [], set()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base + "/", anchor["href"].strip())
        parsed = urlparse(href)
        if not href.startswith("http") or _LISTING_QUERY.search(parsed.query):
            continue
        url = _normalize_url(href)
        if _full_host(url) != base_host:
            continue
        rest = urlparse(url).path[len(base_path):] if urlparse(url).path.startswith(base_path + "/") else ""
        if not rest.startswith("/") or "/" in rest[1:] or len(rest) < 2:
            continue  # not exactly one level below the customers page
        if url in seen:
            continue
        seen.add(url)
        link_text = _normalize_space(anchor.get_text(" ", strip=True)) or _normalize_space(
            " ".join(img.get("alt") or "" for img in anchor.find_all("img"))
        )
        items.append(_item("customer_stories", url, link_text or url, url, "customer_story:child_page"))
    return items


# --- customer logos, for reference only (never scored) ---------------------------

_TRUST_PHRASE = re.compile(r"trusted by|customers", re.IGNORECASE)
_CUSTOMER_NAME = r"[A-Z][\w&'’\-\.]*(?:\s[A-Z][\w&'’\-\.]*){0,2}"
_TRUST_LIST = re.compile(
    r"(?i:trusted by|customers include|customers like|customers such as)\s+"
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
# An entry with any of these, or with 3+ capitalized words, holds several names.
_SEVERAL_NAMES = re.compile(r",|/|\||&|\band\b|\+")


def _company_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _as_brand_set(own_label) -> set:
    if not own_label:
        return set()
    return {own_label} if isinstance(own_label, str) else set(own_label)


def _is_own_brand(name: str, brands: set) -> bool:
    key = _company_key(name)
    first = _company_key(name.split()[0]) if name.split() else ""
    return key in brands or first in brands


def _holds_several_names(name: str) -> bool:
    capitalized = [token for token in name.split() if token[:1].isupper()]
    return bool(_SEVERAL_NAMES.search(name)) or len(capitalized) >= 3


def find_named_customers_in_text(text: str, own_label="") -> list[dict]:
    """Companies listed right after 'trusted by' / 'customers include' wording."""
    brands = _as_brand_set(own_label)
    items = []
    for match in _TRUST_LIST.finditer(text):
        for name in _LIST_SPLIT.split(match.group("list")):
            name = name.strip(" .,;:")
            if not name or name.split()[0].lower() in _NOT_COMPANIES or _company_key(name) == "":
                continue
            if _is_own_brand(name, brands) or _holds_several_names(name):
                continue
            items.append(
                _item("customer_logo_reference", _company_key(name), name,
                      _context(text, match.start(), match.end(), radius=40), "customer:trusted_by_text")
            )
    return items


def find_named_customers_in_html(soup: BeautifulSoup, own_label="") -> list[dict]:
    """
    Logo alt texts inside a customer logo area (an element whose class or id
    says customers/clients/trusted/logo-wall..., or whose own text opens with
    "trusted by" or "customers"), plus customers listed in JSON-LD. Logos in
    the page header or nav, the site's own brand, and alt texts holding
    several names are not counted.
    """
    brands = _as_brand_set(own_label)
    items = []
    for image in soup.find_all("img"):
        alt = _normalize_space(image.get("alt") or "")
        alt = _normalize_space(re.sub(r"\b(?:logo|logotype|icon)\b", "", alt, flags=re.IGNORECASE)).strip(" -–—|:")
        if not (2 <= len(alt) <= 40) or not re.search(r"[A-Za-z]", alt) or alt.lower() in _GENERIC_ALT:
            continue
        if _is_own_brand(alt, brands) or _holds_several_names(alt) or image.find_parent(["header", "nav"]):
            continue
        for depth, ancestor in enumerate(image.parents):
            if depth >= 4 or ancestor.name in ("body", "html", "[document]"):
                break
            marker = f"{' '.join(ancestor.get('class') or [])} {ancestor.get('id') or ''}"
            local_text = ancestor.get_text(" ", strip=True)
            if _LOGO_AREA.search(marker) or (len(local_text) < 1500 and _TRUST_PHRASE.search(local_text[:120])):
                items.append(_item("customer_logo_reference", _company_key(alt), alt, f"logo: {alt}", "customer:logo_area"))
                break
    for node in _json_ld_objects(soup):
        for field in ("customer", "customers", "client", "clients"):
            value = node.get(field)
            for entry in value if isinstance(value, list) else [value]:
                name = entry.get("name") if isinstance(entry, dict) else entry if isinstance(entry, str) else None
                if name and _company_key(name) and not _is_own_brand(name, brands) and not _holds_several_names(name):
                    items.append(_item("customer_logo_reference", _company_key(name), name, f"schema.org {field}: {name}", "customer:schema_org"))
    return items


def _brands_from_page(domain: str, soup: BeautifulSoup) -> set:
    """The site's own brand keys: the domain label plus short segments of the homepage <title>."""
    brands = {_company_key(_host_of(_normalize_url(domain)).split(".")[0])}
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for segment in re.split(r"\s[|–—:·-]\s|\s?[|–—:·]\s?", title):
        segment = segment.strip()
        if segment and len(segment.split()) <= 3 and _company_key(segment):
            brands.add(_company_key(segment))
    brands.discard("")
    return brands


# --- d. third-party ratings and certifications ---------------------------------

# Fixed vocabulary of third-party names: (label, regex, kind). "review" names
# (review sites, analysts) count on any page. "standard" names (security
# and privacy standards) count only on the security page or in a sentence
# that also contains a word from STANDARD_CONTEXT_WORDS, because they are
# often just a list of regulations a product helps with, not something the
# company itself holds. Add a tuple here to teach the engine a new name.
THIRD_PARTY_TERMS = [
    ("G2", re.compile(r"\bG2\b"), "review"),
    ("Capterra", re.compile(r"\bCapterra\b", re.IGNORECASE), "review"),
    ("Trustpilot", re.compile(r"\bTrustpilot\b", re.IGNORECASE), "review"),
    ("Gartner", re.compile(r"\bGartner\b", re.IGNORECASE), "review"),
    ("Forrester", re.compile(r"\bForrester\b", re.IGNORECASE), "review"),
    ("TrustRadius", re.compile(r"\bTrustRadius\b", re.IGNORECASE), "review"),
    ("GetApp", re.compile(r"\bGetApp\b", re.IGNORECASE), "review"),
    ("Software Advice", re.compile(r"\bSoftware Advice\b", re.IGNORECASE), "review"),
    ("SOC 2", re.compile(r"\bSOC\s?2\b", re.IGNORECASE), "standard"),
    ("ISO 27001", re.compile(r"\bISO\s?/?(?:IEC\s)?27001\b", re.IGNORECASE), "standard"),
    ("HIPAA", re.compile(r"\bHIPAA\b", re.IGNORECASE), "standard"),
    ("GDPR", re.compile(r"\bGDPR\b", re.IGNORECASE), "standard"),
    ("PCI DSS", re.compile(r"\bPCI[\s-]?DSS\b", re.IGNORECASE), "standard"),
    ("FedRAMP", re.compile(r"\bFedRAMP\b", re.IGNORECASE), "standard"),
]

STANDARD_CONTEXT_WORDS = [
    "certified", "certification", "compliant", "audit", "attestation", "Type II", "Type 2", "report",
]
_STANDARD_CONTEXT_RE = re.compile(
    r"\b(?:certified|certifications?|compliant|audit(?:s|ed|or|ors)?|attestations?|type ii|type 2|reports?)\b",
    re.IGNORECASE,
)


def find_third_party(text: str, page_kind: str = "home") -> list[dict]:
    """
    One item per THIRD_PARTY_TERMS name found, at its first qualifying
    occurrence on the page. A name inside a question ("Is Acme GDPR
    compliant?") never counts.
    """
    items = []
    for label, pattern, kind in THIRD_PARTY_TERMS:
        for match in pattern.finditer(text):
            sentence = _sentence_at(text, match.start(), match.end())
            if sentence.rstrip().endswith("?"):
                continue
            if kind == "standard" and page_kind != "security":
                if not _STANDARD_CONTEXT_RE.search(sentence):
                    continue
            items.append(
                _item("third_party_source_or_certification", label.lower(), label,
                      _context(text, match.start(), match.end()), f"third_party:{kind}")
            )
            break
    return items


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------

def extract_page_evidence(url: str, kind: str, html: str, own_label="") -> tuple[str, list[dict]]:
    """
    (page text, evidence items) for one page. Items come in a fixed order:
    by category in EVIDENCE_CATEGORIES order, then by the order the rules
    found them in the page, followed by unscored customer logo references.
    Each carries its source page. `own_label` is the site's brand key, or
    a set of them.
    """
    text = extract_text_from_html(html, method="full")
    soup = BeautifulSoup(html, "html.parser")

    logos = []
    if kind in _CUSTOMER_PAGE_KINDS:
        logos = find_named_customers_in_text(text, own_label) + find_named_customers_in_html(soup, own_label)
    by_category = {
        "number_or_statistic": find_numbers(text, kind),
        "customer_stories": find_customer_stories(soup, url) if kind == "customers" else [],
        "attributed_quote": find_attributions(text) + find_html_testimonials(soup),
        "third_party_source_or_certification": find_third_party(text, kind),
    }
    items = []
    for category in EVIDENCE_CATEGORIES:
        for item in by_category[category]:
            items.append({**item, "source_url": url, "source_page": kind})
    for item in logos:
        items.append({**item, "source_url": url, "source_page": kind})
    return text, items


def merge_evidence(per_page_items: list[list[dict]]) -> list[dict]:
    """
    Merge items from pages given in reading order. The same (category, key)
    on several pages, or twice on one page, is one piece of evidence: the
    first occurrence keeps it and other pages are listed in also_found_on.
    """
    merged: dict[tuple[str, str], dict] = {}
    for items in per_page_items:
        for item in items:
            if item["category"] not in EVIDENCE_CATEGORIES:
                continue
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


_BLOCKING_STATUS_CODES = {401, 403, 429, 451}


def _describe_fetch_failure(exc: requests.RequestException) -> str:
    """A short, stable reason: 'blocked: HTTP 403', 'http error: HTTP 404', or 'unreachable: ConnectTimeout'."""
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None):
        code = response.status_code
        return f"{'blocked' if code in _BLOCKING_STATUS_CODES else 'http error'}: HTTP {code}"
    return f"unreachable: {type(exc).__name__}"


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

    Returns score == -1 only when the homepage can't be fetched, with
    details.fetch_failure saying why. A site with no evidence found
    scores 0 with status "ok".
    """
    try:
        homepage_url, homepage_html = _fetch_html(domain)
    except requests.RequestException as exc:
        failure = _describe_fetch_failure(exc)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "fetch_failure": failure,
                "reason": f"Could not read the homepage of '{domain}': {failure}",
                "pages_failed": [{"url": domain, "kind": "home", "error": failure}],
                "plain_finding": {
                    "what_we_found": f"We couldn't read this website. The homepage of {domain} returned: {failure}.",
                    "what_it_costs_you": "",
                    "fix_it_today": "",
                },
            },
        )

    homepage_url = _normalize_url(homepage_url)
    homepage_soup = BeautifulSoup(homepage_html, "html.parser")
    own_brands = _brands_from_page(domain, homepage_soup)

    pages_to_read = [("home", homepage_url, homepage_html)]
    pages_failed = []
    for kind, url in _select_pages(homepage_url, homepage_soup):
        try:
            final_url, html = _fetch_html(url)
        except requests.RequestException as exc:
            pages_failed.append({"url": url, "kind": kind, "error": _describe_fetch_failure(exc)})
            continue
        pages_to_read.append((kind, _normalize_url(final_url), html))

    pages_read, fingerprints, per_page_items = [], [], []
    for kind, url, html in pages_to_read:
        text, items = extract_page_evidence(url, kind, html, own_brands)
        per_page_items.append(items)
        pages_read.append({"kind": kind, "url": url, "text_length": len(text), "evidence_found": len(items)})
        fingerprints.append(page_fingerprint(url, text))

    items = merge_evidence(per_page_items)
    score, counts, points = _score_from_items(items)
    logo_reference, seen_logos = [], set()
    for page_items in per_page_items:
        for item in page_items:
            if item["category"] == "customer_logo_reference" and item["key"] not in seen_logos:
                seen_logos.add(item["key"])
                logo_reference.append(item)

    keys_by_page: dict[str, dict[str, set]] = {c: {} for c in EVIDENCE_CATEGORIES}
    for page_items, page in zip(per_page_items, pages_read):
        for item in page_items:
            if item["category"] in keys_by_page:
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
            "customer_stories_reason": CUSTOMER_STORIES_REASON,
            "customer_logos_reference": logo_reference,
            "own_brands": sorted(own_brands),
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
