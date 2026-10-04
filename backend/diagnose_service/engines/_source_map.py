"""
Source map: which domains actually got cited across citation_engine's
successful Perplexity answers, how often, and whether each one is the
customer's own site.

Pure and offline: no network calls, no AI calls, standard library only.
The same input always produces the same output.
"""

from urllib.parse import urlparse

# Only the top N most-cited domains are returned; a long tail of
# one-off citation sources isn't actionable detail for a founder.
MAX_TOP_DOMAINS = 20


def _extract_domain(url: str) -> str | None:
    """
    Lowercased hostname from `url`, with a leading "www." stripped so
    "www.g2.com" and "g2.com" are treated as the same site. Returns None
    if `url` has no parseable host (missing scheme, malformed, etc.)
    rather than guessing one.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    netloc = parsed.netloc
    if "@" in netloc:
        # Strip userinfo (user:pass@) if present; the host is whatever
        # follows the last "@".
        netloc = netloc.rsplit("@", 1)[-1]
    host = netloc.split(":")[0].lower()  # drop a port, if any

    if not host:
        return None

    if host.startswith("www."):
        host = host[len("www."):]

    return host


def _is_own_domain(host: str, customer_domain: str) -> bool:
    """
    `host` counts as the customer's own site only if it equals
    `customer_domain` exactly, or is a subdomain of it (ends with
    "." + customer_domain). Deliberately not a substring check:
    "notexample.com" must never count as "example.com".
    """
    return host == customer_domain or host.endswith("." + customer_domain)


def build_source_map(domain: str, answers: list[dict]) -> dict:
    """
    Build a citation source map from every successful Perplexity answer.

    `answers` is a list of {"query": str, "citations": list[str]}, one
    entry per successful call. `domain` is the customer's own domain,
    used to flag which cited sites are their own site versus someone
    else's. Returns an empty-but-valid stats shape if `answers` is
    empty, never raises.
    """
    customer_domain = domain.lower()
    if customer_domain.startswith("www."):
        customer_domain = customer_domain[len("www."):]

    total_answers = len(answers)
    unparseable_links = 0
    own_domain_answers = 0

    # domain -> count of distinct answers citing it
    answers_citing: dict[str, int] = {}
    # domain -> set of distinct queries it appeared in
    questions_citing: dict[str, set] = {}

    for answer in answers:
        query = answer.get("query")
        citations = answer.get("citations") or []

        # The same domain cited more than once within one answer still
        # only counts once for that answer.
        domains_in_this_answer: set[str] = set()
        for link in citations:
            host = _extract_domain(link)
            if host is None:
                unparseable_links += 1
                continue
            domains_in_this_answer.add(host)

        if any(_is_own_domain(host, customer_domain) for host in domains_in_this_answer):
            own_domain_answers += 1

        for host in domains_in_this_answer:
            answers_citing[host] = answers_citing.get(host, 0) + 1
            questions_citing.setdefault(host, set()).add(query)

    own_domain_share = round(own_domain_answers / total_answers, 3) if total_answers else 0.0

    sorted_hosts = sorted(answers_citing, key=lambda host: (-answers_citing[host], host))

    top_domains = [
        {
            "domain": host,
            "answers_citing": answers_citing[host],
            "questions_citing": len(questions_citing[host]),
            "share": round(answers_citing[host] / total_answers, 3) if total_answers else 0.0,
            "is_own_domain": _is_own_domain(host, customer_domain),
        }
        for host in sorted_hosts[:MAX_TOP_DOMAINS]
    ]

    return {
        "total_answers": total_answers,
        "unique_domains": len(answers_citing),
        "unparseable_links": unparseable_links,
        "own_domain_share": own_domain_share,
        "top_domains": top_domains,
    }
