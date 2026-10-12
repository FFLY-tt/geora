"""
Fact consistency engine.

Checks whether what third-party review sites (G2, Capterra and similar)
say about a company agrees with the company's own website, on a FIXED
set of fields (FACT_FIELDS): starting price, free plan or trial, product
category, target customer, key integrations, deployment.

Design note: G2 and Capterra both have strong anti-bot protection, so
directly scraping them is unreliable and would require bypassing that
protection, which this project does not do. Instead, this engine asks
Perplexity (which already indexes and cites those sites) for each field's
third-party value and source URL, and treats that as the third-party side.

Why fixed fields and a 3-run majority vote: the previous version let the
LLM list "whatever conflicts it found" in one pass. The same company then
swung between 35 and 10 from run to run, because each run compared a
different set of things. Now every run answers the same six questions,
so runs can be lined up field by field, and a field only counts as a
conflict (or a match) when at least 2 of 3 runs say so. A one-off wrong
answer is outvoted instead of moving the score.

Each run, per field:
- third-party value + source URL from Perplexity. The value only counts if
  the source URL is on a site other than the company's own and matches a
  citation Perplexity actually returned;
- official value + an exact quote from the official text we fetched. A
  quote that can't be found word for word in that text makes the field
  "unknown" for that run;
- a verdict: match / conflict / unknown.

The score keeps the original ladder on the number of voted conflicts
(0/1/2/3+ -> 95/60/35/10). If every field votes "unknown", the score is
-1 ("not enough third-party information"). Only the final voted result is
cached (engines._judgment_cache), keyed on the company, the official
text, and the version of the field list and prompts.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

from shared.config import settings
from shared._fetch_utils import fetch_page_text
from engines._judgment_cache import get_cached_judgment, make_judgment_cache_key, save_judgment
from engines._plain_finding_generator import make_plain_finding
from engines._provenance import page_fingerprint
from models import DimensionScore

DIMENSION_NAME = "fact_consistency"

# Bump whenever this dimension's measurement method changes in a way
# that makes results computed before and after not directly comparable.
METHOD_VERSION = "v2"

# All folded into the judgment cache key, so changing any of them never
# reuses a result produced under the old wording or field list.
THIRD_PARTY_LOOKUP_VERSION = "v2-fixed-fields-lookup"
COMPARISON_PROMPT_VERSION = "v2-fixed-fields-comparison"
FIELDS_VERSION = "v1"

# The fixed fields compared on every run, with the description given to
# both models. Add or change one here and bump FIELDS_VERSION.
FACT_FIELDS = {
    "starting_price": "the price of the cheapest paid plan, as listed",
    "free_plan_or_trial": "whether a free plan or a free trial is offered",
    "product_category": "what kind of product it is",
    "target_customer": "who it is mainly for, for example small businesses, mid-market, or enterprise",
    "key_integrations": "the main integrations it names",
    "deployment": "cloud, on-premise, or both",
}

FACT_FIELD_LABELS = {
    "starting_price": "Starting price",
    "free_plan_or_trial": "Free plan or trial",
    "product_category": "Product category",
    "target_customer": "Target customer",
    "key_integrations": "Key integrations",
    "deployment": "Deployment",
}

RUNS = 3
VERDICTS = ("match", "conflict", "unknown")

# Truncation of the official text sent to the comparison call.
MAX_OFFICIAL_CHARS = 12000

VOTING_RULE = (
    f"Each field is judged on {RUNS} independent runs. It is a conflict only if at least 2 runs say conflict, "
    "a match only if at least 2 runs say match, and unknown otherwise. The score counts voted conflicts: "
    "0, 1, 2, 3 or more give 95, 60, 35, 10."
)

_FIELD_LINES = "\n".join(f"- {name}: {description}" for name, description in FACT_FIELDS.items())
_JSON_SHAPE = ", ".join(f'"{name}": {{"value": <string or null>, "source_url": <string or null>}}' for name in FACT_FIELDS)

_COMPARISON_SYSTEM_PROMPT = f"""
You are a strict fact-checking auditor. You will be given OFFICIAL_TEXT, copied from a company's own
website, and THIRD_PARTY, a JSON object with what third-party review sites say about the company for
a fixed list of fields:
{_FIELD_LINES}

For EACH field in that list:
1. official_value: what OFFICIAL_TEXT says for this field, or null if it does not say.
2. official_quote: the exact sentence or phrase from OFFICIAL_TEXT that supports official_value,
   copied word for word, or an empty string if official_value is null. Never paraphrase.
3. verdict:
   "match" if both sides give a value and they mean the same thing;
   "conflict" if both sides give a value and they genuinely disagree;
   "unknown" if either side has no value, or you cannot tell.

Respond with ONLY a JSON object, no other text, with exactly these keys:
{{{", ".join(f'"{name}": {{"official_value": <string or null>, "official_quote": <string>, "verdict": "match" | "conflict" | "unknown"}}' for name in FACT_FIELDS)}}}
""".strip()


def _build_third_party_query(company_name: str, domain: str) -> str:
    """The Perplexity question: one structured answer per fixed field, from third-party review sites only."""
    return (
        f"Using only what third-party software review sites such as G2, Capterra, TrustRadius, GetApp or "
        f"Software Advice say about {company_name} ({domain}), give a value for each of these fields. "
        "Use null when those sites do not state it. Do not use the company's own website.\n"
        f"{_FIELD_LINES}\n"
        "Respond with ONLY a JSON object, no other text, in this exact shape: "
        f"{{{_JSON_SHAPE}}}"
    )


def _call_perplexity(query: str) -> dict:
    """
    Send `query` to the Perplexity chat completions API and return the
    raw JSON response. Raises requests.RequestException on network/HTTP
    failure so the caller can distinguish "no data" from "empty result".
    """
    url = f"{settings.perplexity_api_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.perplexity_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    payload = {
        "model": settings.perplexity_model,
        "messages": [
            {"role": "user", "content": query},
        ],
        "temperature": 0,
    }
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=settings.request_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def _call_deepseek_for_fact_comparison(company_name: str, official_text: str, third_party_values: dict) -> dict:
    """
    One comparison run: official value, exact quote, and verdict per field.
    Raises requests.RequestException on network/HTTP failure, ValueError
    if the reply isn't a JSON object.
    """
    url = f"{settings.deepseek_api_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    third_party = {name: (third_party_values.get(name) or {}).get("value") for name in FACT_FIELDS}
    user_content = (
        f"Company: {company_name}\n\n"
        f"OFFICIAL_TEXT:\n{official_text[:MAX_OFFICIAL_CHARS]}\n\n"
        f"THIRD_PARTY:\n{json.dumps(third_party, ensure_ascii=False)}"
    )
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": _COMPARISON_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
    }
    response = requests.post(url, headers=headers, json=payload, timeout=settings.request_timeout_seconds)
    response.raise_for_status()
    return _parse_json_object(response.json()["choices"][0]["message"]["content"])


def _parse_json_object(content: str) -> dict:
    """Parse a model reply that should be a JSON object, tolerating code fences and text around it."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", (content or "").strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"No JSON object in reply: {content[:200]!r}")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError(f"Reply is not a JSON object: {parsed!r}")
    return parsed


def _host(url: str) -> str:
    host = urlparse(url if "//" in url else f"//{url}").netloc.split("@")[-1].split(":")[0].lower()
    return host[4:] if host.startswith("www.") else host


def _is_own_site(url: str, domain: str) -> bool:
    own = _host(domain)
    host = _host(url)
    return host == own or host.endswith("." + own)


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _third_party_values(raw_response: dict, domain: str) -> dict:
    """
    {field: {"value", "source_url", "issue"}} from one Perplexity reply. A
    value is kept only if it has a source URL on another site that matches
    one of the citations Perplexity returned; otherwise value is None and
    "issue" says why.
    """
    choices = raw_response.get("choices", [])
    content = choices[0].get("message", {}).get("content", "") if choices else ""
    parsed = _parse_json_object(content)
    citation_hosts = {_host(c) for c in (raw_response.get("citations") or []) if isinstance(c, str)}

    values = {}
    for name in FACT_FIELDS:
        entry = parsed.get(name) if isinstance(parsed.get(name), dict) else {}
        value = entry.get("value")
        value = _normalize_space(str(value)) if value not in (None, "", "null") else None
        source = entry.get("source_url") or None
        issue = None
        if value is None:
            issue = "no third-party value"
        elif not source:
            issue = "no source URL"
        elif _is_own_site(source, domain):
            issue = "source is the company's own site"
        elif _host(source) not in citation_hosts:
            issue = "source URL not among the returned citations"
        values[name] = {
            "value": value if issue is None else None,
            "claimed_value": value,
            "source_url": source if issue is None else None,
            "issue": issue,
        }
    return values


def _judge_run(run_index: int, company_name: str, domain: str, query: str, official_text: str) -> dict:
    """
    One full run: Perplexity lookup, then the comparison call, then our own
    checks (third-party source, verbatim official quote). Never raises: a
    failed run is recorded with status "error" and all its fields unknown.
    """
    try:
        third_party = _third_party_values(_call_perplexity(query), domain)
        comparison = _call_deepseek_for_fact_comparison(company_name, official_text, third_party)
    except (requests.RequestException, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        return {
            "run": run_index,
            "status": "error",
            "error": str(exc),
            "fields": {name: {"verdict": "unknown", "reason": "run failed"} for name in FACT_FIELDS},
        }

    normalized_official = _normalize_space(official_text)
    fields = {}
    for name in FACT_FIELDS:
        tp = third_party[name]
        entry = comparison.get(name) if isinstance(comparison.get(name), dict) else {}
        verdict = entry.get("verdict") if entry.get("verdict") in VERDICTS else "unknown"
        official_value = entry.get("official_value") or None
        quote = _normalize_space(entry.get("official_quote") or "")
        # Never trust the model's claim that a quote is real: it must be
        # found word for word in the text we actually fetched.
        quote_ok = official_value is not None and bool(quote) and quote in normalized_official
        reason = None
        if tp["value"] is None:
            verdict, reason = "unknown", tp["issue"]
        elif official_value is None:
            verdict, reason = "unknown", "no official value"
        elif not quote_ok:
            verdict, reason = "unknown", "official quote not found word for word in the official text"
        fields[name] = {
            "verdict": verdict,
            "reason": reason,
            "third_party_value": tp["value"],
            "third_party_source": tp["source_url"],
            "third_party_claimed_value": tp["claimed_value"],
            "official_value": official_value if quote_ok else None,
            "official_quote": quote if quote_ok else None,
        }
    return {"run": run_index, "status": "ok", "fields": fields}


def vote(verdicts: list[str]) -> str:
    """Majority vote over run verdicts: 2+ conflict -> conflict, 2+ match -> match, otherwise unknown."""
    if verdicts.count("conflict") >= 2:
        return "conflict"
    if verdicts.count("match") >= 2:
        return "match"
    return "unknown"


def _aggregate(runs: list[dict]) -> list[dict]:
    """Per-field voted result, with the third-party and official values from a run that agrees with the vote."""
    fields = []
    for name in FACT_FIELDS:
        votes = [run["fields"][name]["verdict"] for run in runs]
        verdict = vote(votes)
        agreeing = next((run["fields"][name] for run in runs if run["fields"][name]["verdict"] == verdict), {})
        fields.append(
            {
                "field": name,
                "label": FACT_FIELD_LABELS[name],
                "verdict": verdict,
                "votes": votes,
                "third_party_value": agreeing.get("third_party_value"),
                "third_party_source": agreeing.get("third_party_source"),
                "official_value": agreeing.get("official_value"),
                "official_quote": agreeing.get("official_quote"),
            }
        )
    return fields


def _score_from_conflict_count(conflict_count: int) -> int:
    """
    Map the number of conflicting fields to a 0-100 score. No conflicts
    scores high; each additional conflict lowers the score further.
    """
    if conflict_count == 0:
        return 95
    if conflict_count == 1:
        return 60
    if conflict_count == 2:
        return 35
    return 10


def _fetch_official_text(domain: str) -> tuple[str, list[str], list[dict]]:
    """
    Fetch the domain's homepage text, plus a best-effort guess at its
    pricing page. The homepage fetch failing is a hard failure (raised to
    the caller); the pricing-page guess failing is not, since it was only
    a guess, not a known real page.

    Returns (combined_text, fetched_urls, page_fingerprints). Raises
    requests.RequestException if the homepage itself could not be fetched.
    """
    homepage_url = domain if domain.startswith("http") else f"https://{domain}"
    homepage_text = fetch_page_text(domain)
    fetched_urls = [homepage_url]
    fingerprints = [page_fingerprint(homepage_url, homepage_text)]

    pricing_url = f"{homepage_url.rstrip('/')}/pricing"
    try:
        pricing_text = fetch_page_text(pricing_url)
        fetched_urls.append(pricing_url)
        fingerprints.append(page_fingerprint(pricing_url, pricing_text))
    except requests.RequestException:
        pricing_text = ""

    combined_text = homepage_text
    if pricing_text:
        combined_text = f"{homepage_text}\n\n--- Pricing page ---\n\n{pricing_text}"

    return combined_text, fetched_urls, fingerprints


_DIMENSION_NOTE = (
    "Dimension: whether facts on the company's own website match what independent listings such as "
    "review sites say about it.\n"
)


def _insufficient(reason: str, fallback: str, status_note: str, **extra) -> DimensionScore:
    plain_finding, generation_log = make_plain_finding(_DIMENSION_NOTE + f"Status: {status_note}", fallback)
    return DimensionScore(
        dimension=DIMENSION_NAME,
        score=-1,
        details={
            "status": "insufficient_data",
            "reason": reason,
            **extra,
            "generation_log": generation_log,
            "plain_finding": plain_finding,
        },
    )


def _cache_key(company_name: str, domain: str, official_text: str) -> str:
    cache_input = (
        f"COMPANY: {company_name}\nDOMAIN: {domain}\nFIELDS: {','.join(FACT_FIELDS)}\n\n"
        f"OFFICIAL:\n{official_text[:MAX_OFFICIAL_CHARS]}"
    )
    version = f"{THIRD_PARTY_LOOKUP_VERSION}|{COMPARISON_PROMPT_VERSION}|fields-{FIELDS_VERSION}"
    model = f"{settings.perplexity_model}|{settings.deepseek_model}"
    return make_judgment_cache_key(DIMENSION_NAME, version, model, cache_input)


def check_fact_consistency(company_name: str, domain: str) -> DimensionScore:
    """
    Compare FACT_FIELDS between `domain`'s official site and third-party
    review sites, by a 3-run majority vote per field (see module docstring).

    Returns score == -1 ("insufficient data") when the official site can't
    be fetched, when the needed API keys are missing, when every run
    fails, or when every field votes unknown (not enough third-party
    information). Never fabricates a conflict or a match.
    """
    try:
        official_text, official_urls_fetched, page_fingerprints = _fetch_official_text(domain)
    except requests.RequestException:
        return _insufficient(
            f"Failed to fetch domain homepage '{domain}'",
            "We couldn't open the official homepage to compare against what independent listings say, "
            "so this check can't finish. Today, try loading the homepage yourself to confirm it's reachable.",
            f"We could not open the official homepage at {domain}.",
        )
    if not official_text:
        return _insufficient(
            f"Homepage for '{domain}' returned no extractable body text (possibly JS-rendered).",
            "We found no readable text on the official homepage to compare against independent listings. "
            "Today, check that your homepage's core copy shows up right away for a visitor.",
            "The official homepage had no readable text.",
            official_urls_fetched=official_urls_fetched,
        )

    query = _build_third_party_query(company_name, domain)
    cache_key = _cache_key(company_name, domain, official_text)
    cached = get_cached_judgment(DIMENSION_NAME, cache_key)
    if cached is not None:
        fields, runs, cache_status = cached["fields"], cached["runs"], "cache_hit"
    else:
        if not settings.has_perplexity_key() or not settings.has_deepseek_key():
            return _insufficient(
                "PERPLEXITY_API_KEY or DEEPSEEK_API_KEY is not configured; no comparison was performed.",
                "We couldn't check whether independent listings agree with the official site, because a "
                "required connection isn't turned on yet. Today, turn on that connection so this check can run.",
                "A required connection is not turned on.",
                official_urls_fetched=official_urls_fetched,
            )
        with ThreadPoolExecutor(max_workers=RUNS) as executor:
            runs = list(
                executor.map(
                    lambda index: _judge_run(index, company_name, domain, query, official_text),
                    range(1, RUNS + 1),
                )
            )
        if all(run["status"] == "error" for run in runs):
            return _insufficient(
                "Every third-party lookup or comparison run failed: "
                + "; ".join(f"run {run['run']}: {run['error']}" for run in runs),
                "We tried looking up what independent review sites say about this brand, but the request "
                "failed this time, so we don't have a real comparison. Today, just try running the report again.",
                "Requests to outside tools failed this time.",
                query=query,
                runs=runs,
                official_urls_fetched=official_urls_fetched,
            )
        fields = _aggregate(runs)
        cache_status = "fresh_judgment"
        if any(field["verdict"] != "unknown" for field in fields):
            save_judgment(DIMENSION_NAME, cache_key, {"fields": fields, "runs": runs})

    conflicts = [f for f in fields if f["verdict"] == "conflict"]
    matches = [f for f in fields if f["verdict"] == "match"]
    common = {
        "query": query,
        "fields": fields,
        "runs": runs,
        "voting_rule": VOTING_RULE,
        "fields_version": FIELDS_VERSION,
        "cache_status": cache_status,
        "official_urls_fetched": official_urls_fetched,
        "page_fingerprints": page_fingerprints,
    }

    if not conflicts and not matches:
        return _insufficient(
            "Not enough third-party information: every field voted unknown.",
            f'We looked up what independent review sites say about "{company_name}" for six basic facts, '
            "like starting price and who the product is for, and could not confirm any of them against "
            "the official site. Today, make sure your G2 or Capterra listing spells out pricing, plans, and "
            "integrations, the same way your site does.",
            "Independent review sites gave too little information to compare.",
            **common,
        )

    score = _score_from_conflict_count(len(conflicts))
    fallback = _build_fallback_plain_finding(company_name, matches, conflicts)
    facts_summary = _build_facts_summary(company_name, matches, conflicts)
    plain_finding, generation_log = make_plain_finding(facts_summary, fallback)

    return DimensionScore(
        dimension=DIMENSION_NAME,
        score=score,
        details={
            "status": "ok",
            **common,
            "conflict_fields": [f["field"] for f in conflicts],
            "match_fields": [f["field"] for f in matches],
            "unknown_fields": [f["field"] for f in fields if f["verdict"] == "unknown"],
            "generation_log": generation_log,
            "plain_finding": plain_finding,
        },
    )


def _build_facts_summary(company_name: str, matches: list, conflicts: list) -> str:
    """Plain-text real facts handed to the AI writer — see engines._plain_finding_generator."""
    checked = ", ".join(f["label"].lower() for f in matches + conflicts)
    if not conflicts:
        return (
            _DIMENSION_NOTE
            + f'We compared {checked} on the official site against independent listings for "{company_name}". '
            "They agree on all of it."
        )
    lines = " ".join(
        f'On {f["label"].lower()}, the official site says: {f["official_value"]} Independent listings say: '
        f'{f["third_party_value"]}'
        for f in conflicts
    )
    return _DIMENSION_NOTE + f"We compared {checked} and found a real mismatch. {lines}"


def _build_fallback_plain_finding(company_name: str, matches: list, conflicts: list) -> str:
    """Founder-readable finding built only from the voted fields, used if AI writing fails."""
    checked = ", ".join(f["label"].lower() for f in matches + conflicts)
    if not conflicts:
        return (
            f'We compared {checked} on the official site against what independent listings say about '
            f'"{company_name}", and found no disagreement. Customers who cross-check review sites before '
            "buying won't find anything that makes them doubt the official claims. Today, keep both sides "
            "updated together when pricing or plans change."
        )
    lines = "; ".join(
        f'on {f["label"].lower()}, the official site says "{f["official_value"]}" while independent listings '
        f'say "{f["third_party_value"]}"'
        for f in conflicts
    )
    return (
        f"We compared the official site against independent listings and found a real mismatch: {lines}. "
        "A customer who checks both places before buying will hit conflicting information right when they "
        "decide whether to trust this brand. Today, update whichever side is out of date."
    )
