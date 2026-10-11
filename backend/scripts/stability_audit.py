"""
Measure how stable a repeated diagnosis of ONE company is, across all 6
scoring engines, and write a report — this script never changes any
engine's scoring logic. It only calls the real check_* functions and
observes what happens.

Why: real-world reports showed the same company's fact_consistency
score swinging between 95, 35, and 10 within a few days. This script
answers "where exactly does that come from" for every dimension, not
just fact_consistency: does the set of pages read change between runs?
Does a page's own content change (the external world changing) between
runs? Or is the input to an LLM call identical every time, but its
output still differs (the model's own non-determinism)?

How observability works without touching any engine file: this script
wraps `requests.get` and `requests.post` (see _install_http_logging)
with a thin, read-only logger that records the call's URL, a SHA256
hash + length of a GET response body, and for a POST (every LLM/
Perplexity call in this codebase goes through requests.post with a
JSON body) the temperature the call used (if any — Perplexity's calls
set none), a SHA256 hash of the input, and the raw output text plus
its hash. The wrapper always calls straight through to the real
requests.get/requests.post and returns its exact, unmodified result —
no engine's behavior changes, this is pure observation. No engine
source file is edited by this script.

Buyer questions for citation_visibility: this script does nothing
special to force question reuse across runs — it relies on
citation_engine's frozen per-category question sets
(engines/_question_sets.py, committed under state/), which make every
run after the first reuse the exact same questions. The report
verifies this held and says so explicitly, rather than assuming it.

--no-judgment-cache: evidence_density and topic_relevance now cache
their aggregated judgment per exact input (engines._judgment_cache,
added after this script first proved that was needed). That cache
makes a 5-run stability check of range 0 trivially true from run 2
onward regardless of whether the underlying listing/judgment method
is itself stable — it would only prove the cache works, not that the
method does. Passing --no-judgment-cache monkeypatches
evidence_engine/relevance_engine's get_cached_judgment (always a miss)
and save_judgment (a no-op) for the life of this process, so every
single run performs a genuinely fresh LLM call with no cache
involved at all — see _install_judgment_cache_bypass. This never
touches any engine's scoring logic, only which function object those
two names resolve to within these two modules.

Usage:
    python backend/scripts/stability_audit.py "Feroot Security" feroot.com
    python backend/scripts/stability_audit.py "Feroot Security" feroot.com --runs 5 --yes
    python backend/scripts/stability_audit.py "Feroot Security" feroot.com --runs 5 --yes --no-judgment-cache

Output (see OUTPUT_DIR):
  <domain>_runs.jsonl   — one line per run, every dimension's score,
                          details, and the HTTP calls made while
                          computing it.
  report_<domain>.md    — the human-readable stability report.
"""

import argparse
import hashlib
import json
import math
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import requests

_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
_SERVICE_DIR = _BACKEND_DIR / "diagnose_service"

sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))

import engines.citation_engine as citation_engine  # noqa: E402
import engines.evidence_engine as evidence_engine  # noqa: E402
import engines.recency_engine as recency_engine  # noqa: E402
import engines.fact_engine as fact_engine  # noqa: E402
import engines.relevance_engine as relevance_engine  # noqa: E402
import engines.technical_engine as technical_engine  # noqa: E402

OUTPUT_DIR = _THIS_DIR / "output" / "stability"

SECONDS_BETWEEN_RUNS = 5

DIMENSION_ORDER = [
    "citation_visibility",
    "topic_relevance",
    "evidence_density",
    "content_recency",
    "fact_consistency",
    "technical_readability",
]

# A score swing of more than this many points across runs triggers the
# detailed step-by-step divergence analysis in the report.
RANGE_FLAG_THRESHOLD = 5


def _install_judgment_cache_bypass() -> list:
    """
    See --no-judgment-cache in the module docstring. Returns the list
    of started patchers so the caller can .stop() them; does not
    modify any engine source file or scoring logic, only which
    function object get_cached_judgment/save_judgment resolve to
    within evidence_engine/relevance_engine for the life of this
    process.
    """
    patchers = [
        patch.object(evidence_engine, "get_cached_judgment", return_value=None),
        patch.object(evidence_engine, "save_judgment", lambda *args, **kwargs: None),
        patch.object(relevance_engine, "get_cached_judgment", return_value=None),
        patch.object(relevance_engine, "save_judgment", lambda *args, **kwargs: None),
    ]
    for patcher in patchers:
        patcher.start()
    return patchers

# Fields/snippet length kept per LLM call so the report is readable
# without being a second copy of every raw answer.
OUTPUT_SNIPPET_CHARS = 300


# --------------------------------------------------------------------------
# Read-only HTTP call logging — see module docstring.
# --------------------------------------------------------------------------

_call_log: list[dict] = []
_call_log_lock = threading.Lock()
_real_requests_get = requests.get
_real_requests_post = requests.post


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def _call_log_mark() -> int:
    """Current length of the call log, used as the start of a time window to slice later."""
    with _call_log_lock:
        return len(_call_log)


def _call_log_since(start: int) -> list[dict]:
    with _call_log_lock:
        return list(_call_log[start:])


def _logging_get(url, *args, **kwargs):
    try:
        response = _real_requests_get(url, *args, **kwargs)
    except requests.RequestException as exc:
        with _call_log_lock:
            _call_log.append({"kind": "http_get", "url": url, "status": "error", "error": str(exc)})
        raise

    body = response.text or ""
    with _call_log_lock:
        _call_log.append(
            {
                "kind": "http_get",
                "url": url,
                "status": "ok",
                "status_code": response.status_code,
                "final_url": getattr(response, "url", url),
                "content_length": len(body),
                "content_sha256": _sha256(body),
            }
        )
    return response


def _extract_post_input_text(json_payload) -> str:
    if not isinstance(json_payload, dict):
        return ""
    if "messages" in json_payload:
        return json.dumps(json_payload["messages"], sort_keys=True, ensure_ascii=False)
    return json.dumps(json_payload, sort_keys=True, ensure_ascii=False)


def _extract_post_output_text(response) -> str | None:
    try:
        parsed = response.json()
        return parsed["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def _logging_post(url, *args, **kwargs):
    json_payload = kwargs.get("json")
    temperature = json_payload.get("temperature") if isinstance(json_payload, dict) else None
    input_text = _extract_post_input_text(json_payload)

    try:
        response = _real_requests_post(url, *args, **kwargs)
    except requests.RequestException as exc:
        with _call_log_lock:
            _call_log.append(
                {
                    "kind": "http_post",
                    "url": url,
                    "status": "error",
                    "error": str(exc),
                    "temperature": temperature,
                    "input_sha256": _sha256(input_text),
                }
            )
        raise

    output_text = _extract_post_output_text(response)
    provider = "deepseek" if "deepseek" in url else "perplexity" if "perplexity" in url else "unknown"
    with _call_log_lock:
        _call_log.append(
            {
                "kind": "http_post",
                "url": url,
                "status": "ok",
                "status_code": response.status_code,
                "provider": provider,
                "temperature": temperature,
                "input_sha256": _sha256(input_text),
                "output_sha256": _sha256(output_text) if output_text is not None else None,
                "output_snippet": (output_text or "")[:OUTPUT_SNIPPET_CHARS],
            }
        )
    return response


def _install_http_logging() -> None:
    """Replace requests.get/requests.post with the logging wrappers above, for the life of this process."""
    requests.get = _logging_get
    requests.post = _logging_post


# --------------------------------------------------------------------------
# Running the 6 engines once, with the call log sliced per dimension.
# --------------------------------------------------------------------------


def _run_all_dimensions(company_name: str, domain: str) -> dict:
    """
    Run all 6 engines once, sequentially (so each one's HTTP calls can
    be cleanly attributed to it by time window), and return
    {dimension_name: {"score": int, "details": dict, "http_calls": [...]}}.
    """
    dimensions: dict[str, dict] = {}

    start = _call_log_mark()
    citation_result = citation_engine.check_citation_visibility(company_name, domain)
    dimensions["citation_visibility"] = {
        "score": citation_result.score,
        "details": citation_result.details,
        "http_calls": _call_log_since(start),
    }

    start = _call_log_mark()
    evidence_result = evidence_engine.check_evidence_density(domain)
    dimensions["evidence_density"] = {
        "score": evidence_result.score,
        "details": evidence_result.details,
        "http_calls": _call_log_since(start),
    }

    start = _call_log_mark()
    recency_result = recency_engine.check_recency(domain)
    dimensions["content_recency"] = {
        "score": recency_result.score,
        "details": recency_result.details,
        "http_calls": _call_log_since(start),
    }

    start = _call_log_mark()
    fact_result = fact_engine.check_fact_consistency(company_name, domain)
    dimensions["fact_consistency"] = {
        "score": fact_result.score,
        "details": fact_result.details,
        "http_calls": _call_log_since(start),
    }

    start = _call_log_mark()
    technical_result = technical_engine.check_technical_readability(domain)
    dimensions["technical_readability"] = {
        "score": technical_result.score,
        "details": technical_result.details,
        "http_calls": _call_log_since(start),
    }

    start = _call_log_mark()
    relevance_result = relevance_engine.check_topic_relevance(domain, citation_result)
    dimensions["topic_relevance"] = {
        "score": relevance_result.score,
        "details": relevance_result.details,
        "http_calls": _call_log_since(start),
    }

    return dimensions


def _citation_mention_totals(citation_details: dict) -> tuple[int, int]:
    """
    Roll up total mentions / total countable answers from
    citation_visibility's own per-run results — the same "countable"
    definition check_citation_visibility itself uses (a run counts
    only if it got a real answer and wasn't classified "uncertain").
    A plain count of numbers the engine already computed per run, not
    a re-judgment of anything.
    """
    per_query_results = citation_details.get("per_query_results") or []
    mentions = 0
    total_answers = 0
    for entry in per_query_results:
        for run in entry.get("runs", []):
            if run.get("status") != "ok" or run.get("mention_classification") == "uncertain":
                continue
            total_answers += 1
            if run.get("is_hit"):
                mentions += 1
    return mentions, total_answers


# --------------------------------------------------------------------------
# Wilson 95% confidence interval for a binomial proportion.
# --------------------------------------------------------------------------

_Z_95 = 1.959963985


def wilson_interval(successes: int, total: int, z: float = _Z_95) -> tuple[float, float] | None:
    """
    Wilson score interval for `successes` out of `total` trials.
    Returns None if total is 0 — there is no interval to compute.
    """
    if total == 0:
        return None
    phat = successes / total
    denominator = 1 + z**2 / total
    center = (phat + z**2 / (2 * total)) / denominator
    margin = (z * math.sqrt((phat * (1 - phat) / total) + (z**2 / (4 * total**2)))) / denominator
    lower = max(0.0, center - margin)
    upper = min(1.0, center + margin)
    return lower, upper


# --------------------------------------------------------------------------
# Divergence analysis: for a dimension with a flagged range, work out
# which step first differs across the N runs.
# --------------------------------------------------------------------------


def _get_calls(http_calls: list[dict]) -> list[dict]:
    return [c for c in http_calls if c["kind"] == "http_get"]


def _post_calls(http_calls: list[dict]) -> list[dict]:
    return [c for c in http_calls if c["kind"] == "http_post"]


def _analyze_pages_read(per_run_http_calls: list[list[dict]]) -> dict:
    """
    Compare the set of URLs read, and (for URLs read in 2+ runs) their
    content hash, across runs. Returns a dict describing what, if
    anything, differed — never raises, and makes no judgment about an
    engine's correctness, only reports what was observed.
    """
    url_sets = [frozenset(c["url"] for c in _get_calls(calls) if c["status"] == "ok") for calls in per_run_http_calls]
    failed_per_run = [[c for c in _get_calls(calls) if c["status"] == "error"] for calls in per_run_http_calls]
    urls_identical = len(set(url_sets)) <= 1

    hash_by_url: dict[str, list[str | None]] = {}
    for run_index, calls in enumerate(per_run_http_calls):
        for call in _get_calls(calls):
            if call["status"] != "ok":
                continue
            hash_by_url.setdefault(call["url"], [None] * len(per_run_http_calls))[run_index] = (
                f"{call['content_sha256'][:12]} ({call['content_length']} chars)"
            )

    content_changes = {
        url: hashes for url, hashes in hash_by_url.items() if len(set(h for h in hashes if h is not None)) > 1
    }

    any_fetch_failures = any(failed_per_run)

    return {
        "url_sets": url_sets,
        "urls_identical": urls_identical,
        "hash_by_url": hash_by_url,
        "content_changes": content_changes,
        "fetch_failures": failed_per_run,
        "any_fetch_failures": any_fetch_failures,
    }


def _analyze_llm_calls(per_run_http_calls: list[list[dict]]) -> dict:
    """
    Pool every LLM/Perplexity POST call across ALL runs for this
    dimension, grouped by its exact input hash. For any input that was
    sent 2+ times total (whether within one run's own repeats or across
    different runs), check whether every one of those calls produced
    the same output hash. An input repeated with 2+ distinct outputs is
    direct, order-independent proof that the model itself is not
    deterministic on that exact input — not that something upstream of
    it changed, since the input is proven identical.
    """
    all_calls = [call for calls in per_run_http_calls for call in _post_calls(calls) if call["status"] == "ok"]

    by_input: dict[str, list[dict]] = {}
    for call in all_calls:
        by_input.setdefault(call["input_sha256"], []).append(call)

    repeated_inputs = {h: calls for h, calls in by_input.items() if len(calls) >= 2}
    noisy_inputs = {
        h: calls for h, calls in repeated_inputs.items() if len({c["output_sha256"] for c in calls}) > 1
    }

    temperatures = sorted({call["temperature"] for call in all_calls}, key=lambda t: (t is None, t))

    return {
        "total_calls": len(all_calls),
        "distinct_inputs": len(by_input),
        "repeated_inputs_count": len(repeated_inputs),
        "noisy_inputs": noisy_inputs,
        "temperatures_seen": temperatures,
    }


def _divergence_report(dimension_name: str, records: list[dict]) -> str:
    per_run_http_calls = [record["dimensions"][dimension_name]["http_calls"] for record in records]
    pages = _analyze_pages_read(per_run_http_calls)
    llm = _analyze_llm_calls(per_run_http_calls)

    lines = [f"#### Step-by-step divergence for `{dimension_name}`", ""]

    if pages["any_fetch_failures"]:
        lines.append("- **A page fetch failed in at least one run.** Details:")
        for run_index, failures in enumerate(pages["fetch_failures"], start=1):
            for failure in failures:
                lines.append(f"  - run {run_index}: `{failure['url']}` -> {failure['error']}")
    else:
        lines.append("- No page fetch failed in any run.")

    if pages["urls_identical"]:
        urls = sorted(pages["url_sets"][0]) if pages["url_sets"] and pages["url_sets"][0] else []
        if urls:
            lines.append(f"- Pages read: **identical set across all runs** ({', '.join(urls)}).")
        else:
            lines.append("- No pages were fetched directly by this dimension in any run.")
    else:
        lines.append("- **Pages read differed between runs.** Per-run URL sets:")
        for run_index, urls in enumerate(pages["url_sets"], start=1):
            lines.append(f"  - run {run_index}: {sorted(urls) if urls else '(none)'}")

    if pages["content_changes"]:
        lines.append("- **Content hash differed for a page read in more than one run** (the external page itself changed, or we got a different version of it):")
        for url, hashes in pages["content_changes"].items():
            lines.append(f"  - `{url}`: {hashes}")
    elif pages["hash_by_url"]:
        lines.append("- For every page read in more than one run, its content hash was **identical** every time.")

    if llm["total_calls"] == 0:
        lines.append("- No LLM/Perplexity calls were made for this dimension (purely deterministic code).")
    else:
        lines.append(
            f"- {llm['total_calls']} LLM/Perplexity call(s) made across all runs, "
            f"{llm['distinct_inputs']} distinct input(s) seen, {llm['repeated_inputs_count']} of them repeated "
            f"2+ times. Temperature value(s) observed: {llm['temperatures_seen']}."
        )
        if llm["noisy_inputs"]:
            lines.append(
                f"- **Found {len(llm['noisy_inputs'])} exact input(s) that were sent more than once and got "
                "different outputs.** This is direct evidence of model non-determinism on fixed input, "
                "independent of anything upstream:"
            )
            for input_hash, calls in llm["noisy_inputs"].items():
                outputs = {c["output_sha256"][:12]: c["output_snippet"] for c in calls}
                lines.append(f"  - input `{input_hash[:12]}` sent {len(calls)} times, {len(outputs)} distinct outputs:")
                for output_hash, snippet in outputs.items():
                    lines.append(f"    - `{output_hash}`: {snippet!r}")
        elif llm["repeated_inputs_count"] > 0:
            lines.append(
                "- Every input that was repeated 2+ times always produced the identical output. No model "
                "non-determinism was directly observed on a fixed input — but most inputs were NOT repeated, "
                "so this does not rule it out for those."
            )
        else:
            lines.append(
                "- No input was repeated across runs (e.g. a non-cached search query changes wording each "
                "time), so this analysis can't isolate model noise from upstream change — only that the "
                "inputs themselves were never identical twice."
            )

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Report assembly.
# --------------------------------------------------------------------------


def _score_summary_table(records: list[dict]) -> tuple[str, dict[str, dict]]:
    stats_by_dimension: dict[str, dict] = {}
    header = "| dimension | " + " | ".join(f"run {r['run']}" for r in records) + " | min | max | range |"
    separator = "|---" * (len(records) + 4) + "|"
    rows = [header, separator]

    for dimension_name in DIMENSION_ORDER:
        scores = [record["dimensions"][dimension_name]["score"] for record in records]
        real_scores = [s for s in scores if s != -1]
        if real_scores:
            score_min, score_max = min(real_scores), max(real_scores)
            score_range = score_max - score_min
        else:
            score_min, score_max, score_range = None, None, None
        stats_by_dimension[dimension_name] = {"scores": scores, "min": score_min, "max": score_max, "range": score_range}

        score_cells = " | ".join(str(s) for s in scores)
        range_cell = str(score_range) if score_range is not None else "n/a (always -1)"
        rows.append(
            f"| {dimension_name} | {score_cells} | "
            f"{score_min if score_min is not None else 'n/a'} | {score_max if score_max is not None else 'n/a'} | "
            f"{range_cell} |"
        )

    return "\n".join(rows), stats_by_dimension


def _citation_mention_section(records: list[dict]) -> str:
    lines = ["## Citation mention rate and Wilson 95% confidence interval", ""]
    header = "| run | mentions | total_answers | mention_rate | wilson_95_ci |"
    lines.append(header)
    lines.append("|---|---|---|---|---|")

    pooled_mentions = 0
    pooled_total = 0
    for record in records:
        details = record["dimensions"]["citation_visibility"]["details"]
        mentions, total = _citation_mention_totals(details)
        pooled_mentions += mentions
        pooled_total += total
        rate = round(mentions / total, 3) if total else "n/a"
        ci = wilson_interval(mentions, total)
        ci_text = f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "n/a"
        lines.append(f"| {record['run']} | {mentions} | {total} | {rate} | {ci_text} |")

    pooled_rate = round(pooled_mentions / pooled_total, 3) if pooled_total else "n/a"
    pooled_ci = wilson_interval(pooled_mentions, pooled_total)
    pooled_ci_text = f"[{pooled_ci[0]:.3f}, {pooled_ci[1]:.3f}]" if pooled_ci else "n/a"
    lines.append("")
    lines.append(
        f"Pooled across all {len(records)} runs: {pooled_mentions}/{pooled_total} = {pooled_rate}, "
        f"Wilson 95% CI {pooled_ci_text}."
    )
    return "\n".join(lines)


def _buyer_questions_section(records: list[dict]) -> str:
    first_details = records[0]["dimensions"]["citation_visibility"]["details"]
    first_generation_log = first_details.get("generation_log") or []
    first_run_was_cache_hit = any(entry.get("outcome") == "cache_hit" for entry in first_generation_log) or (
        (first_details.get("question_set") or {}).get("source") in ("existing_company_assignment", "matched_existing_category")
    )

    per_run_questions = []
    for record in records:
        details = record["dimensions"]["citation_visibility"]["details"]
        per_query_results = details.get("per_query_results") or []
        questions = tuple(sorted(entry["query"] for entry in per_query_results if entry.get("query")))
        per_run_questions.append(questions)

    distinct_question_sets = set(per_run_questions)
    all_same = len(distinct_question_sets) <= 1

    category = first_details.get("category", "(not available)")
    lines = [
        "## Buyer questions used for citation_visibility",
        "",
        f"Run 1's buyer-question generation was a **{'cache hit (reused an existing, still-fresh entry)' if first_run_was_cache_hit else 'fresh generation'}**.",
        f"Category used: {category!r}",
        "",
    ]
    if all_same and per_run_questions[0]:
        lines.append(f"Same question set was used in **all {len(records)} runs** (citation_engine's frozen question set held):")
        for q in per_run_questions[0]:
            lines.append(f"- {q}")
    elif not per_run_questions[0]:
        lines.append("No questions were available at all (question generation failed on run 1); this audit cannot say anything about citation_visibility's question-stability for this company.")
    else:
        lines.append("**Question sets differed between runs** (the frozen question set did NOT hold as expected) — per run:")
        for record, questions in zip(records, per_run_questions):
            lines.append(f"- run {record['run']}: {list(questions)}")

    return "\n".join(lines)


def _evidence_density_category_table(records: list[dict]) -> str:
    """
    Per-run breakdown of how many verified evidence items landed in
    each of evidence_density's 4 categories (see
    engines/evidence_engine.py's EVIDENCE_CATEGORIES), so a reviewer
    can see exactly which category is driving a score change rather
    than only the final score. Returns "" if no run has this
    structured breakdown available (e.g. every run was
    insufficient_data).
    """
    categories: list[str] | None = None
    rows = []
    for record in records:
        details = record["dimensions"]["evidence_density"]["details"]
        counts = details.get("counts_by_category")
        if counts is None:
            continue
        if categories is None:
            categories = list(counts.keys())
        rows.append(
            [str(record["run"])]
            + [str(counts.get(category, "")) for category in categories]
            + [str(record["dimensions"]["evidence_density"]["score"])]
        )

    if categories is None:
        return ""

    header_cells = ["run"] + categories + ["score"]
    lines = [
        "#### Evidence category counts per run (`evidence_density`)",
        "",
        "| " + " | ".join(header_cells) + " |",
        "|" + "---|" * len(header_cells),
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def build_report(company_name: str, domain: str, records: list[dict], judgment_cache_bypassed: bool = False) -> str:
    generated_at = datetime.now(timezone.utc).isoformat()
    score_table, stats_by_dimension = _score_summary_table(records)
    cache_note = (
        "Judgment cache: **bypassed** for this run (--no-judgment-cache) — every run performed a fresh "
        "LLM judgment with no caching involved at all."
        if judgment_cache_bypassed
        else "Judgment cache: enabled (default) — a run after the first may be a cache hit; see each "
        "dimension's own details for cache_status."
    )

    sections = [
        f"# Stability audit: {company_name} ({domain})",
        "",
        f"Generated: {generated_at}",
        f"Runs: {len(records)}",
        cache_note,
        "",
        _buyer_questions_section(records),
        "",
        "## Score summary",
        "",
        score_table,
        "",
        "## Dimension-by-dimension analysis",
        "",
    ]

    for dimension_name in DIMENSION_ORDER:
        stats = stats_by_dimension[dimension_name]
        sections.append(f"### {dimension_name}")
        sections.append("")
        if stats["range"] is None:
            sections.append("Every run returned -1 (insufficient data) — no real score to measure stability on.")
        elif stats["range"] <= RANGE_FLAG_THRESHOLD:
            sections.append(f"Range = {stats['range']} (<= {RANGE_FLAG_THRESHOLD}). Stable; no further analysis.")
        else:
            sections.append(f"**Range = {stats['range']} (> {RANGE_FLAG_THRESHOLD}).** Step-by-step divergence analysis follows.")
            sections.append("")
            sections.append(_divergence_report(dimension_name, records))
        sections.append("")

        if dimension_name == "evidence_density":
            category_table = _evidence_density_category_table(records)
            if category_table:
                sections.append(category_table)
                sections.append("")

    sections.append(_citation_mention_section(records))
    sections.append("")
    sections.append("## Notes")
    sections.append("")
    sections.append(
        "- This script only calls the existing check_* engine functions and observes requests.get/requests.post "
        "calls; it does not change any engine's scoring logic (see this script's module docstring)."
    )
    sections.append(
        "- A dimension's score of -1 (insufficient data) is excluded from its min/max/range; such a run is "
        "called out explicitly above where relevant."
    )
    sections.append(
        "- A dimension's \"LLM/Perplexity call(s)\" tally includes BOTH that dimension's own scoring "
        "judgment call(s) AND the shared plain-language-finding writer call (engines._plain_finding_generator"
        ".make_plain_finding), since both are requests.post calls to the same DeepSeek endpoint and can't be "
        "told apart by URL alone. The writer call's input depends on which reasoning/wording the scoring step "
        "happened to produce that run, so it is expected to appear as its own one-off, never-repeated input — "
        "it does not add a second independent source of instability beyond the scoring call itself."
    )

    return "\n".join(sections) + "\n"


# --------------------------------------------------------------------------
# CLI.
# --------------------------------------------------------------------------


def _estimated_calls_per_run() -> tuple[int, int]:
    """(perplexity_calls, deepseek_calls) upper-bound estimate for ONE run, from the engines' own constants."""
    perplexity_calls = citation_engine.REQUIRED_QUERY_COUNT * citation_engine.RUNS_PER_QUERY + 1  # + fact_engine
    deepseek_calls = (
        2  # citation_engine: category + query generation (0 on a cache hit, which is most runs here)
        + evidence_engine.JUDGMENT_RUNS
        + (citation_engine.REQUIRED_QUERY_COUNT * relevance_engine.JUDGMENT_RUNS_PER_QUESTION)
        + 1  # fact_engine's comparison call
    )
    return perplexity_calls, deepseek_calls


def _confirm(runs: int, skip_confirmation: bool) -> bool:
    perplexity_per_run, deepseek_per_run = _estimated_calls_per_run()
    print(f"Runs requested: {runs}")
    print(f"Estimated Perplexity calls: up to {runs * perplexity_per_run}")
    print(
        f"Estimated DeepSeek calls: up to {runs * deepseek_per_run} "
        "(citation_engine's question-set calls only happen the first time a company is ever diagnosed)"
    )
    if skip_confirmation:
        return True
    return input("Proceed? [y/N]: ").strip().lower() == "y"


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure diagnosis stability for one company across repeated runs.")
    parser.add_argument("company_name")
    parser.add_argument("domain")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument(
        "--no-judgment-cache",
        action="store_true",
        help=(
            "Bypass evidence_engine/relevance_engine's judgment cache entirely for this run (neither read "
            "nor write), so every run performs a fresh LLM judgment instead of a cache hit from run 2 onward"
        ),
    )
    args = parser.parse_args()

    if not _confirm(args.runs, args.yes):
        print("Aborted.")
        return

    _install_http_logging()
    judgment_cache_patchers = _install_judgment_cache_bypass() if args.no_judgment_cache else []
    if args.no_judgment_cache:
        print("--no-judgment-cache: every run below performs a fresh LLM judgment, no caching involved.")

    results_path = OUTPUT_DIR / f"{args.domain}_runs.jsonl"
    records: list[dict] = []

    try:
        for run_index in range(1, args.runs + 1):
            print(f"Run {run_index}/{args.runs} for {args.company_name} ({args.domain})...")
            dimensions = _run_all_dimensions(args.company_name, args.domain)
            record = {
                "run": run_index,
                "company_name": args.company_name,
                "domain": args.domain,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "dimensions": dimensions,
            }
            records.append(record)
            _append_jsonl(results_path, record)
            scores = {name: dimensions[name]["score"] for name in DIMENSION_ORDER}
            print(f"  scores: {scores}")

            if run_index < args.runs:
                time.sleep(SECONDS_BETWEEN_RUNS)
    finally:
        for patcher in judgment_cache_patchers:
            patcher.stop()

    report = build_report(args.company_name, args.domain, records, judgment_cache_bypassed=args.no_judgment_cache)
    report_path = OUTPUT_DIR / f"report_{args.domain}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    print(f"Done. Records: {results_path}  Report: {report_path}")


if __name__ == "__main__":
    main()
