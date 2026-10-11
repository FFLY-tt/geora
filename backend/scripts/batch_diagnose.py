"""
Batch-run diagnose_service's 6 scoring engines over a list of companies
read from a CSV file.

Imports the real check_* functions from diagnose_service/engines/ and
calls them directly — no scoring logic is duplicated here. Companies
are processed strictly one at a time, never in parallel, with a pause
between each (see SECONDS_BETWEEN_COMPANIES): this script makes many
more outbound requests back to back than a single ad-hoc diagnosis, so
it has to be a better-behaved visitor than the live product is.

Unlike the live /api/diagnose route, this script bypasses
result_cache.py's 24-hour result cache entirely (it is simply never
imported), since the whole point of a batch run is a fresh diagnosis
for every company. citation_engine's frozen per-category question sets
and the LLM judgment cache (both committed under
diagnose_service/state/) still apply — they are what keep the scores
reproducible, not something a batch run should defeat. Each record
carries the same `provenance` the live API returns.

Output (see OUTPUT_DIR):
  results.jsonl  — one JSON object per company, the full DimensionScore
                   details for every dimension, appended immediately
                   after that company finishes.
  summary.csv    — one row per company with the headline numbers, same
                   append-immediately behavior.

Resuming: on startup, results.jsonl is read to find which domains
already have a result, and those are skipped — no network calls, no
re-write. Re-running this script after an interruption simply picks up
where it left off.

A single company failing (an unexpected exception from the diagnosis
itself) is caught, recorded with status "error", and the batch moves
on to the next company rather than stopping.

Before touching any site, this script reads that site's robots.txt and
checks whether our User-Agent may fetch the homepage at all; if not,
the company is skipped entirely (status "skipped_robots") and no
diagnosis is attempted. If the homepage turns out to redirect to a
different domain (e.g. the company was acquired), the company is still
diagnosed normally, but the record is flagged status "redirected" with
the domain it landed on, so that's visible in the summary.

Usage:
    python backend/scripts/batch_diagnose.py companies.csv
    python backend/scripts/batch_diagnose.py companies.csv --yes

See backend/README.md's "Batch diagnosis" section.
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
_SERVICE_DIR = _BACKEND_DIR / "diagnose_service"

# Make both backend/ ("shared") and backend/diagnose_service/ ("engines",
# "models") importable, the same sys.path setup server.py and the
# diagnose_service tests use.
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))

from shared.config import settings  # noqa: E402
import engines.citation_engine as citation_engine  # noqa: E402
import engines.evidence_engine as evidence_engine  # noqa: E402
import engines.recency_engine as recency_engine  # noqa: E402
import engines.fact_engine as fact_engine  # noqa: E402
import engines.relevance_engine as relevance_engine  # noqa: E402
import engines.technical_engine as technical_engine  # noqa: E402
from provenance import build_provenance  # noqa: E402

OUTPUT_DIR = _THIS_DIR / "output"
RESULTS_FILE = OUTPUT_DIR / "results.jsonl"
SUMMARY_FILE = OUTPUT_DIR / "summary.csv"

# Minimum pause between companies. Not a rate limit on any single API —
# this just keeps a batch run from hammering a string of unrelated
# third-party sites back to back.
SECONDS_BETWEEN_COMPANIES = 5

SUMMARY_FIELDNAMES = [
    "company_name",
    "domain",
    "status",
    "mentions",
    "total_answers",
    "mention_rate",
    "topic_relevance",
    "evidence_density",
    "content_recency",
    "fact_consistency",
    "technical_readability",
    "own_site_cited_share",
    "top_cited_1",
    "top_cited_2",
    "top_cited_3",
    "name_is_common_word",
    "error",
]


def _build_user_agent() -> str:
    """
    This script's own User-Agent for its direct requests (robots.txt
    checks, redirect detection) — settings.http_user_agent plus a
    contact email read from settings/.env (CONTACT_EMAIL), never
    hardcoded. A batch run makes far more outbound requests than one
    ad-hoc diagnosis, so a site operator who wants to block or ask
    about this traffic needs a way to reach someone.
    """
    if settings.contact_email:
        return f"{settings.http_user_agent} (contact: {settings.contact_email})"
    return settings.http_user_agent


def _robots_allows_homepage(domain: str, user_agent: str) -> bool:
    """
    Fetch robots.txt for `domain` and return whether `user_agent` may
    fetch the homepage. No robots.txt at all (a 404, or the request
    itself failing) is treated as allowed, per robots.txt's own spec:
    no file means no restriction. Mirrors
    engines/technical_engine.py's _check_crawler_access, but checks
    this script's own scraping User-Agent against the homepage rather
    than the 4 AI-crawler agents that engine audits.
    """
    full_url = domain if domain.startswith("http") else f"https://{domain}"
    robots_url = full_url.rstrip("/") + "/robots.txt"

    try:
        response = requests.get(
            robots_url, headers={"User-Agent": user_agent}, timeout=settings.request_timeout_seconds
        )
        if response.status_code == 404:
            robots_content = ""
        else:
            response.raise_for_status()
            robots_content = response.text
    except requests.RequestException:
        robots_content = ""

    parser = RobotFileParser()
    parser.parse(robots_content.splitlines())
    return parser.can_fetch(user_agent, full_url.rstrip("/") + "/")


def _normalized_host(url_or_domain: str) -> str:
    """Lowercased hostname with a leading "www." stripped, for comparing two domains loosely."""
    full_url = url_or_domain if url_or_domain.startswith("http") else f"https://{url_or_domain}"
    host = (urlparse(full_url).netloc or "").lower()
    if host.startswith("www."):
        host = host[len("www."):]
    return host


def _detect_redirect(domain: str, user_agent: str) -> str | None:
    """
    Fetch `domain`'s homepage and return the host it actually landed on
    if that host is a different domain (e.g. the company was
    acquired and now redirects elsewhere), or None if it stayed on the
    same domain or the request failed. Best-effort only: a failure
    here does not raise, it just means no redirect could be detected —
    the full diagnosis that follows still runs and reports its own
    "insufficient data" if the site really is unreachable.
    """
    full_url = domain if domain.startswith("http") else f"https://{domain}"
    try:
        response = requests.get(
            full_url, headers={"User-Agent": user_agent}, timeout=settings.request_timeout_seconds
        )
    except requests.RequestException:
        return None

    landed_host = _normalized_host(response.url)
    original_host = _normalized_host(domain)
    return landed_host if landed_host and landed_host != original_host else None


def _run_full_diagnosis(company_name: str, domain: str) -> list[dict]:
    """
    Run all 6 engines for one company and return the same `dimensions`
    list shape server.py's /api/diagnose returns, just computed
    sequentially instead of concurrently (no need for asyncio in an
    offline batch job) and never touching result_cache.
    """
    citation_result = citation_engine.check_citation_visibility(company_name, domain)
    evidence_result = evidence_engine.check_evidence_density(domain)
    recency_result = recency_engine.check_recency(domain)
    fact_result = fact_engine.check_fact_consistency(company_name, domain)
    technical_result = technical_engine.check_technical_readability(domain)
    relevance_result = relevance_engine.check_topic_relevance(domain, citation_result)

    return [
        citation_result.model_dump(),
        relevance_result.model_dump(),
        evidence_result.model_dump(),
        recency_result.model_dump(),
        fact_result.model_dump(),
        technical_result.model_dump(),
    ]


def _load_completed_domains() -> set[str]:
    """Domains that already have a line in results.jsonl from a previous (possibly interrupted) run."""
    if not RESULTS_FILE.exists():
        return set()

    completed = set()
    with open(RESULTS_FILE, "r", encoding="utf-8") as file_handle:
        for line in file_handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            domain = record.get("domain")
            if domain:
                completed.add(domain)
    return completed


def _append_result_record(record: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_FILE, "a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _citation_mention_totals(citation_dimension: dict) -> tuple[int, int]:
    """
    Roll up total mentions / total countable answers directly from
    citation_visibility's own per-run results — the same "countable"
    definition check_citation_visibility itself uses (a run counts
    only if it got a real answer and wasn't classified "uncertain" by
    the common-word disambiguation step). This is a plain count of
    numbers the engine already computed per run, not a re-judgment of
    anything.
    """
    per_query_results = (citation_dimension.get("details") or {}).get("per_query_results") or []
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


def _build_summary_row(record: dict) -> dict:
    row = {field: "" for field in SUMMARY_FIELDNAMES}
    row["company_name"] = record["company_name"]
    row["domain"] = record["domain"]
    row["status"] = record["status"]
    row["error"] = record.get("error") or ""

    dimensions = record.get("dimensions")
    if not dimensions:
        return row

    by_dimension = {d["dimension"]: d for d in dimensions}

    citation = by_dimension.get("citation_visibility")
    if citation:
        mentions, total_answers = _citation_mention_totals(citation)
        row["mentions"] = mentions
        row["total_answers"] = total_answers
        row["mention_rate"] = round(mentions / total_answers, 3) if total_answers else ""
        row["name_is_common_word"] = (citation.get("details") or {}).get("name_is_common_word", "")

        source_map = (citation.get("details") or {}).get("source_map") or {}
        row["own_site_cited_share"] = source_map.get("own_domain_share", "")
        top_domains = source_map.get("top_domains") or []
        for position in range(3):
            row[f"top_cited_{position + 1}"] = (
                top_domains[position]["domain"] if position < len(top_domains) else ""
            )

    for dimension_name in (
        "topic_relevance",
        "evidence_density",
        "content_recency",
        "fact_consistency",
        "technical_readability",
    ):
        dimension = by_dimension.get(dimension_name)
        if dimension:
            row[dimension_name] = dimension["score"]

    return row


def _append_summary_row(record: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    file_has_header = SUMMARY_FILE.exists() and SUMMARY_FILE.stat().st_size > 0
    with open(SUMMARY_FILE, "a", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=SUMMARY_FIELDNAMES)
        if not file_has_header:
            writer.writeheader()
        writer.writerow(_build_summary_row(record))


def _save_record(
    company_name: str,
    domain: str,
    status: str,
    redirected_domain: str | None = None,
    dimensions: list[dict] | None = None,
    error: str | None = None,
) -> None:
    record = {
        "company_name": company_name,
        "domain": domain,
        "status": status,
        "redirected_domain": redirected_domain,
        "dimensions": dimensions,
        "provenance": build_provenance(dimensions) if dimensions else None,
        "error": error,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    _append_result_record(record)
    _append_summary_row(record)


def _process_company(company_name: str, domain: str) -> None:
    print(f"Processing {company_name} ({domain})...")
    user_agent = _build_user_agent()

    if not _robots_allows_homepage(domain, user_agent):
        print("  Skipped: robots.txt disallows our User-Agent on the homepage.")
        _save_record(company_name, domain, status="skipped_robots")
        return

    redirected_domain = _detect_redirect(domain, user_agent)

    try:
        dimensions = _run_full_diagnosis(company_name, domain)
    except Exception as exc:  # noqa: BLE001 - one company's failure must never abort the batch
        print(f"  Error: {exc}")
        _save_record(company_name, domain, status="error", error=str(exc))
        return

    status = "redirected" if redirected_domain else "ok"
    if redirected_domain:
        print(f"  Redirected to {redirected_domain}")
    print(f"  Done: status={status}")
    _save_record(company_name, domain, status=status, redirected_domain=redirected_domain, dimensions=dimensions)


def _read_companies_csv(path: Path) -> list[dict]:
    with open(path, "r", newline="", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        return [
            {"company_name": row["company_name"].strip(), "domain": row["domain"].strip()}
            for row in reader
            if row.get("company_name") and row.get("domain")
        ]


def _estimated_calls_per_company() -> tuple[int, int]:
    """
    (perplexity_calls, deepseek_calls) upper-bound estimate for ONE
    company, derived from the engines' own constants rather than
    hardcoded. The real DeepSeek count is usually lower: citation
    _engine's category+query generation (2 calls) is skipped entirely
    on a buyer-question cache hit, and several calls have their own
    one-time retry that doesn't always fire.
    """
    perplexity_calls = citation_engine.REQUIRED_QUERY_COUNT * citation_engine.RUNS_PER_QUERY + 1  # + fact_engine
    deepseek_calls = (
        2  # citation_engine: category classification + query generation (0 on a cache hit)
        + evidence_engine.JUDGMENT_RUNS
        + (citation_engine.REQUIRED_QUERY_COUNT * relevance_engine.JUDGMENT_RUNS_PER_QUESTION)
        + 1  # fact_engine's comparison call
    )
    return perplexity_calls, deepseek_calls


def _confirm(remaining_count: int, skip_confirmation: bool) -> bool:
    perplexity_per_company, deepseek_per_company = _estimated_calls_per_company()
    print(f"Companies to process: {remaining_count}")
    print(f"Estimated Perplexity calls: up to {remaining_count * perplexity_per_company}")
    print(
        f"Estimated DeepSeek calls: up to {remaining_count * deepseek_per_company} "
        "(lower for any company whose buyer questions are already cached from a previous run)"
    )

    if skip_confirmation:
        return True

    return input("Proceed? [y/N]: ").strip().lower() == "y"


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-run diagnose_service's engines over a CSV of companies.")
    parser.add_argument("companies_csv", type=Path, help="CSV file with company_name,domain columns")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    companies = _read_companies_csv(args.companies_csv)
    completed_domains = _load_completed_domains()
    remaining = [c for c in companies if c["domain"] not in completed_domains]

    already_done_count = len(companies) - len(remaining)
    if already_done_count:
        print(f"Skipping {already_done_count} company(ies) that already have a result in {RESULTS_FILE}")

    if not remaining:
        print("Nothing to do: every company in the input file already has a result.")
        return

    if not _confirm(len(remaining), args.yes):
        print("Aborted.")
        return

    for index, company in enumerate(remaining):
        _process_company(company["company_name"], company["domain"])
        if index < len(remaining) - 1:
            time.sleep(SECONDS_BETWEEN_COMPANIES)

    print(f"Done. Results: {RESULTS_FILE}  Summary: {SUMMARY_FILE}")


if __name__ == "__main__":
    main()
