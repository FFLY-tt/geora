"""
Topic relevance engine.

Reuses the raw answer text already fetched by citation_engine (no extra
API calls here) and uses simple string-matching heuristics to judge how
closely the sentences that mention the company actually relate to the
question that was asked, versus the company name showing up as an
unrelated, out-of-context mention.
"""

import re

from engines._plain_finding_generator import make_plain_finding
from models import DimensionScore

DIMENSION_NAME = "topic_relevance"

# A small, generic stopword list used only to avoid inflating overlap
# scores with common filler words. Not meant to be linguistically complete.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "best", "by", "do", "does",
    "for", "how", "in", "is", "it", "of", "on", "or", "that", "the", "to",
    "top", "was", "what", "who", "with", "worth", "you", "2024", "2025",
}


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokenizer that strips punctuation and stopwords."""
    words = re.findall(r"[a-zA-Z0-9']+", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _split_sentences(text: str) -> list[str]:
    """Naive sentence splitter, good enough for heuristic matching."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _sentence_relevance(query: str, sentence: str) -> float:
    """
    Jaccard overlap between query tokens and sentence tokens: a crude but
    dependency-free proxy for "does this sentence actually talk about
    what the query asked, or is the company just mentioned in passing".
    """
    query_tokens = _tokenize(query)
    sentence_tokens = _tokenize(sentence)
    if not query_tokens or not sentence_tokens:
        return 0.0
    overlap = query_tokens & sentence_tokens
    union = query_tokens | sentence_tokens
    return len(overlap) / len(union) if union else 0.0


def check_topic_relevance(citation_result: DimensionScore) -> DimensionScore:
    """
    Estimate how relevant the AI answers' mentions of the company are to
    the questions asked, using only data already collected by
    citation_engine (citation_result.details["raw_answers"]).

    Returns score == -1 ("insufficient data") when citation_engine itself
    could not obtain any real data, since there is nothing to analyze.
    """
    if citation_result.score == -1:
        fallback = (
            "We couldn't check this because the earlier step that tests whether AI mentions this "
            "brand at all didn't produce a real result to build on. See the citation visibility "
            "finding above for what went wrong there. Fixing that check will let this one run too. "
            "There's nothing separate to do here today."
        )
        facts_summary = (
            "Dimension: whether AI search answers that mention this company actually stay on topic for "
            "the question a customer asked, instead of giving a vague or generic answer.\n"
            "Status: An earlier, required check did not produce usable results, so this check has "
            "nothing to examine yet."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": "citation_engine produced no data to analyze (see citation_visibility dimension).",
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    raw_answers: dict = citation_result.details.get("raw_answers", {})

    if not raw_answers:
        fallback = (
            "We didn't have any AI answer text left over from the earlier brand-mention check to "
            "look at, so there's nothing here to judge. Today, just re-run the report. This step "
            "feeds off that earlier check and should have real answers to examine next time."
        )
        facts_summary = (
            "Dimension: whether AI search answers that mention this company actually stay on topic for "
            "the question a customer asked, instead of giving a vague or generic answer.\n"
            "Status: The earlier check ran, but left no answer text behind for this check to examine."
        )
        plain_finding, generation_log = make_plain_finding(facts_summary, fallback)
        return DimensionScore(
            dimension=DIMENSION_NAME,
            score=-1,
            details={
                "status": "insufficient_data",
                "reason": "No raw answer text was available from citation_engine.",
                "generation_log": generation_log,
                "plain_finding": plain_finding,
            },
        )

    per_query_relevance = []
    relevant_scores = []

    for query, answer_text in raw_answers.items():
        if not answer_text:
            per_query_relevance.append({
                "query": query,
                "best_sentence": None,
                "relevance": 0.0,
                "note": "Empty answer text.",
            })
            continue

        sentences = _split_sentences(answer_text)
        best_sentence = ""
        best_score = 0.0
        for sentence in sentences:
            score = _sentence_relevance(query, sentence)
            if score > best_score:
                best_score = score
                best_sentence = sentence

        per_query_relevance.append({
            "query": query,
            "best_sentence": best_sentence or None,
            "relevance": round(best_score, 3),
        })
        relevant_scores.append(best_score)

    if not relevant_scores:
        overall_score = 0
    else:
        overall_score = round(100 * sum(relevant_scores) / len(relevant_scores))

    fallback = _build_fallback_plain_finding(overall_score, per_query_relevance)
    facts_summary = _build_facts_summary(overall_score, per_query_relevance)
    plain_finding, generation_log = make_plain_finding(facts_summary, fallback)

    return DimensionScore(
        dimension=DIMENSION_NAME,
        score=overall_score,
        details={
            "status": "ok",
            "method": "Jaccard word-overlap between each query and its best-matching answer sentence.",
            "per_query_relevance": per_query_relevance,
            "generation_log": generation_log,
            "plain_finding": plain_finding,
        },
    )


# A per-query relevance score at or above this is treated as "the AI answer
# actually engaged with that specific question" for the plain_finding below.
_DIRECT_ANSWER_THRESHOLD = 0.15


def _build_facts_summary(overall_score: int, per_query_relevance: list) -> str:
    """
    Plain-text real facts handed to the AI writer — see
    engines._plain_finding_generator. Deliberately does NOT include raw
    query/answer text: those routinely contain citation markers, markdown,
    and dense technical jargon that would leak straight through if quoted.
    """
    total = len(per_query_relevance)
    direct_count = sum(1 for entry in per_query_relevance if entry.get("relevance", 0) >= _DIRECT_ANSWER_THRESHOLD)
    off_topic_count = total - direct_count
    # Deliberately state both counts plainly instead of picking a verb like
    # "mostly ignored" based on the score band: a phrase such as "ignored
    # what was asked in 0 of them" reads as the opposite of a bad score
    # once direct_count is low, which misled the model into writing an
    # upbeat summary for a genuinely bad result. Two explicit counts can't
    # be misread in either direction.
    return (
        "Dimension: whether AI search answers that mention this company actually stay on topic for the "
        "question a customer asked, instead of giving a vague or generic answer.\n"
        f"Out of {total} real customer questions tested, the AI's answer directly addressed what was "
        f"actually asked in {direct_count} of them, and drifted off topic or stayed too vague to count "
        f"as a real answer in {off_topic_count} of them."
    )


def _build_fallback_plain_finding(overall_score: int, per_query_relevance: list) -> str:
    """Founder-readable finding built only from this run's real counts, used if AI writing fails."""
    total = len(per_query_relevance)
    direct_count = sum(1 for entry in per_query_relevance if entry.get("relevance", 0) >= _DIRECT_ANSWER_THRESHOLD)

    if overall_score >= 70:
        return (
            "When customers asked specific, real-world questions in this category, the AI's answers "
            "directly engaged with what was actually asked instead of talking around it. That's a good "
            "sign. A customer who gets a precise, on-point answer is far more likely to trust the source "
            "than one who gets a generic response. Today, keep publishing content that answers specific "
            "questions head on. It's clearly paying off."
        )

    if overall_score <= 30:
        return (
            "When customers asked specific, real-world questions in this category, the AI's answers "
            "mostly talked around the question instead of directly and plainly answering it. Long on "
            "background, short on a clear bottom line. That kind of roundabout response is unlikely to "
            "build a customer's trust, even in cases where this brand does get mentioned somewhere in it. "
            "Today, publish a page that states the direct answer to one of these specific questions in "
            "its very first sentence, instead of leading with general background."
        )

    return (
        f"When customers asked specific, real-world questions in this category, the AI's answers were "
        f"direct and on-point for {direct_count} out of {total} of them, and vague or roundabout for the "
        "rest. That's a mixed signal. Some customers get a precise, trust-building answer, and others get "
        "something generic. Today, pick one of the questions that got a roundabout answer and publish a "
        "page that states the direct answer plainly, up front."
    )
