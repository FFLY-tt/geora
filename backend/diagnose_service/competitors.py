"""
Optional competitor comparison.

Counts, in the answers citation_visibility already collected, how many mention
each competitor the user typed in, with the same rule that decides whether an
answer mentions the user's own company (citation_engine.classify_mention). It
makes no Perplexity call and touches no other dimension. When no competitor
was given there is nothing to compute and no result block at all.

Input is one text field: up to MAX_COMPETITORS entries, comma separated, each
"Name" or "Name (domain.com)". The parsed list is stored with the result, and
`signature` of it is part of what the cached result is keyed on, so changing the
list recounts from the stored answers instead of asking Perplexity again.
"""

import json
import re

from engines import citation_engine

MAX_COMPETITORS = 5
SNIPPET_LENGTH = 240

_ENTRY_WITH_DOMAIN = re.compile(r"^(?P<name>.+?)\s*\(\s*(?P<domain>[^()]*?)\s*\)$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


class CompetitorInputError(ValueError):
    """The competitor text can't be used; the message is safe to show to the user."""


def _clean_domain(raw: str) -> str:
    domain = raw.strip().lower()
    domain = re.sub(r"^[a-z][a-z0-9+.-]*://", "", domain)
    domain = domain.split("/")[0].split("?")[0].split("#")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def parse_competitors(raw: str | None, own_company_name: str = "", own_domain: str = "") -> list[dict]:
    """
    [{"name", "domain"}] in the order typed ("domain" is "" when none was given).
    Empty or blank input is an empty list. Repeats (same name, or same domain)
    and the user's own company are dropped. More than MAX_COMPETITORS entries
    raises CompetitorInputError rather than silently ignoring some.
    """
    if raw is None or not raw.strip():
        return []

    own_name = own_company_name.strip().lower()
    own_domain = _clean_domain(own_domain)
    seen_names: set[str] = set()
    seen_domains: set[str] = set()
    competitors: list[dict] = []

    for item in re.split(r"[,，;；\n]", raw):
        item = item.strip()
        if not item:
            continue
        match = _ENTRY_WITH_DOMAIN.match(item)
        name = (match.group("name") if match else item).strip()
        domain = _clean_domain(match.group("domain")) if match else ""
        if not name:
            continue
        if domain and "." not in domain:
            raise CompetitorInputError(f'"{domain}" doesn\'t look like a website address. Try something like pipedrive.com.')
        if name.lower() == own_name or (domain and domain == own_domain):
            continue
        if name.lower() in seen_names or (domain and domain in seen_domains):
            continue
        seen_names.add(name.lower())
        if domain:
            seen_domains.add(domain)
        competitors.append({"name": name, "domain": domain})

    if len(competitors) > MAX_COMPETITORS:
        raise CompetitorInputError(f"Add at most {MAX_COMPETITORS} competitors.")
    return competitors


def signature(competitors: list[dict] | None) -> str:
    """A stable text for a competitor list; "" for none. Equal lists give equal text."""
    if not competitors:
        return ""
    return json.dumps([[c["name"].lower(), c["domain"]] for c in competitors], ensure_ascii=False)


def _answers(citation_details: dict) -> list[dict]:
    """Every successfully collected answer: {question, answer_text, citations, run}."""
    answers = []
    for entry in citation_details.get("per_query_results") or []:
        for run in entry.get("runs") or []:
            if run.get("status") == "ok":
                answers.append(
                    {
                        "question": entry.get("query", ""),
                        "answer_text": run.get("answer_text") or "",
                        "citations": run.get("all_citations") or [],
                        "run": run,
                    }
                )
    return answers


def _snippet(name: str, domain: str, answer_text: str, judged: dict) -> str:
    """The sentence of the answer that shows the mention, shortened; falls back to the start of the answer."""
    sentence = judged.get("evidence_sentence")
    if not sentence:
        for candidate in _SENTENCE_SPLIT.split(answer_text):
            if candidate.strip() and citation_engine._company_name_mentioned(name, candidate):
                sentence = candidate.strip()
                break
    if not sentence and judged.get("candidate_sentences"):
        sentence = judged["candidate_sentences"][0]
    if not sentence:
        sentence = f"(cited {domain})" if domain else answer_text.strip()
    sentence = " ".join(sentence.split())
    return sentence if len(sentence) <= SNIPPET_LENGTH else sentence[: SNIPPET_LENGTH - 1].rstrip() + "…"


def _entry(name: str, domain: str, is_you: bool, judged_answers: list[tuple[dict, dict]]) -> dict:
    """One company's count over (answer, classification) pairs; uncertain answers are not counted either way."""
    countable = [(a, j) for a, j in judged_answers if j["mention_classification"] != "uncertain"]
    hits = [(a, j) for a, j in countable if j["is_hit"]]
    return {
        "name": name,
        "domain": domain,
        "is_you": is_you,
        "mentions": len(hits),
        "total": len(countable),
        "mentioned_in": [
            {
                "question": a["question"],
                "rule": j["mention_rule"],
                "snippet": _snippet(name, domain, a["answer_text"], j),
            }
            for a, j in hits
        ],
    }


def compare_competitors(
    company_name: str, domain: str, competitors: list[dict], citation_details: dict
) -> dict | None:
    """
    The comparison block, or None when there is nothing to compare (no competitor
    given, or no answer was collected). Uses only the answers stored in
    `citation_details`; the user's own company is counted from the same stored
    per-answer verdicts the mention rate was built from.
    """
    if not competitors:
        return None
    answers = _answers(citation_details)
    if not answers:
        return None

    category = citation_details.get("category") or ""
    entries = [_entry(company_name, domain, True, [(a, a["run"]) for a in answers])]
    for competitor in competitors:
        judged = [
            (
                a,
                citation_engine.classify_mention(
                    competitor["name"], competitor["domain"], category, a["answer_text"], a["citations"]
                ),
            )
            for a in answers
        ]
        entries.append(_entry(competitor["name"], competitor["domain"], False, judged))

    # Most mentions first; on a tie the user's company first, then the order typed. sorted() is stable.
    ordered = sorted(entries, key=lambda e: (-e["mentions"], not e["is_you"]))
    return {"competitors": competitors, "entries": ordered}
