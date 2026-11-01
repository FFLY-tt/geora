"""
Local JSON-file cache for full diagnosis results, keyed by domain.

Individual engine calls have residual run-to-run variance even after
freezing the buyer questions (see engines/_question_sets.py and the
citation_visibility/topic_relevance investigation) — Perplexity's own
answer generation is not deterministic even for an identical query.
Rather than chasing that at the engine level, this cache makes the whole
/api/diagnose response itself stable for a fixed 24-hour window per
domain: a repeated check against the same domain within that window
always returns the exact same result, computed once. There is
deliberately no way to force a recompute from the request — see
server.py's diagnose route.

This is a per-machine cache (backend/diagnose_service/data/, gitignored),
not shared state: deleting it only forces a recompute, which must give
the same five reason scores because the question sets and judgments it
depends on live in the committed state/ directory.

Uses the same read-modify-write-with-file-lock pattern as
backend/leads_store.py.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    # fcntl is POSIX-only. This project only targets macOS/Linux, so on
    # any other platform reads/writes simply fall back to unsynchronized.
    _HAS_FCNTL = False

_DATA_DIR = Path(__file__).parent / "data"
_CACHE_FILE = _DATA_DIR / "result_cache.json"

CACHE_TTL = timedelta(hours=24)


def _read_cache(file_handle) -> dict:
    """Read and parse the current contents of the cache file, if any."""
    file_handle.seek(0)
    raw_content = file_handle.read()
    if not raw_content.strip():
        return {}
    parsed = json.loads(raw_content)
    if not isinstance(parsed, dict):
        raise ValueError(f"Result cache file '{_CACHE_FILE}' does not contain a JSON object.")
    return parsed


def get_cached_result(domain: str) -> dict | None:
    """
    Return the cached diagnosis result dict for `domain` (company_name,
    domain, dimensions, cached_at) if an entry younger than CACHE_TTL
    exists, else None.

    Any problem reading/parsing the cache (missing file, corrupted JSON,
    malformed entry) is treated as a cache miss rather than raised — a
    broken cache should never block a diagnosis from running.
    """
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as file_handle:
            if _HAS_FCNTL:
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_SH)
            try:
                cache = _read_cache(file_handle)
            finally:
                if _HAS_FCNTL:
                    fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return None

    entry = cache.get(domain)
    if not isinstance(entry, dict):
        return None

    cached_at_raw = entry.get("cached_at")
    if not cached_at_raw:
        return None

    try:
        cached_at = datetime.fromisoformat(cached_at_raw)
    except ValueError:
        return None

    if datetime.now(timezone.utc) - cached_at > CACHE_TTL:
        return None

    return entry


def _read_entry(domain: str) -> dict | None:
    """The raw stored entry for `domain` regardless of age, or None if missing/unreadable."""
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as file_handle:
            if _HAS_FCNTL:
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_SH)
            try:
                cache = _read_cache(file_handle)
            finally:
                if _HAS_FCNTL:
                    fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return None

    entry = cache.get(domain)
    return entry if isinstance(entry, dict) else None


def get_previous_provenance(domain: str) -> dict | None:
    """
    The `provenance` of whatever entry currently exists for `domain`
    (expired or not), or None — including for entries stored before
    provenance was recorded. The frontend only compares scores when the
    previous and current method/question-set versions match. Call this
    BEFORE save_result overwrites the entry.
    """
    entry = _read_entry(domain)
    if entry is None:
        return None
    provenance = entry.get("provenance")
    return provenance if isinstance(provenance, dict) else None


def get_previous_citation_interval(domain: str) -> dict | None:
    """
    {mentions, total, ci_low, ci_high} from the previous result's
    citation_visibility details, or None if there is no previous result
    or it predates the interval fields. Lets the frontend tell a real
    change from noise (overlapping intervals). Call this BEFORE save_result.
    """
    entry = _read_entry(domain)
    if entry is None:
        return None
    for dim in entry.get("dimensions") or []:
        if isinstance(dim, dict) and dim.get("dimension") == "citation_visibility":
            details = dim.get("details") or {}
            if details.get("ci_low") is None or details.get("ci_high") is None:
                return None
            return {key: details.get(key) for key in ("mentions", "total", "ci_low", "ci_high")}
    return None


def update_cached_competitors(domain: str, competitors: list[dict] | None, comparison: dict | None) -> dict | None:
    """
    Replace the competitor list and comparison stored with `domain`'s entry,
    leaving everything else, including `cached_at`, untouched (recounting
    competitors from stored answers does not make the diagnosis any newer).
    Returns the updated entry, or None if there is no entry.
    """
    try:
        with open(_CACHE_FILE, "r+", encoding="utf-8") as file_handle:
            if _HAS_FCNTL:
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX)
            try:
                cache = _read_cache(file_handle)
                entry = cache.get(domain)
                if not isinstance(entry, dict):
                    return None
                entry["competitors"] = competitors or None
                entry["competitor_comparison"] = comparison
                file_handle.seek(0)
                file_handle.truncate()
                json.dump(cache, file_handle, indent=2, ensure_ascii=False)
                return entry
            finally:
                if _HAS_FCNTL:
                    fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return None


def get_previous_scores(domain: str) -> dict | None:
    """
    Return {dimension: score} from whatever entry currently exists for
    `domain`, regardless of whether it is still within CACHE_TTL — an
    expired entry's scores still count as "the previous result" for a
    before/after comparison. Returns None if no entry exists at all (this
    domain has never been checked before), which the caller should treat
    as "nothing to compare against", not as all-zero scores.

    Call this BEFORE save_result overwrites the entry for the same domain.
    """
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as file_handle:
            if _HAS_FCNTL:
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_SH)
            try:
                cache = _read_cache(file_handle)
            finally:
                if _HAS_FCNTL:
                    fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return None

    entry = cache.get(domain)
    if not isinstance(entry, dict):
        return None

    dimensions = entry.get("dimensions")
    if not isinstance(dimensions, list):
        return None

    scores = {
        dim["dimension"]: dim["score"]
        for dim in dimensions
        if isinstance(dim, dict) and "dimension" in dim and "score" in dim
    }
    return scores or None


def save_result(domain: str, result: dict) -> dict:
    """
    Store `result` (a dict with company_name/domain/dimensions) for
    `domain`, stamped with the current time, and return the stored entry
    (result plus `cached_at`) so the caller can return it immediately
    without a second read.

    Creates backend/data/ if it does not exist. Uses a read-modify-write
    cycle guarded by an exclusive file lock, same pattern as
    leads_store.save_lead.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    entry = {**result, "cached_at": datetime.now(timezone.utc).isoformat()}

    with open(_CACHE_FILE, "a+", encoding="utf-8") as file_handle:
        if _HAS_FCNTL:
            fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX)
        try:
            try:
                cache = _read_cache(file_handle)
            except (ValueError, json.JSONDecodeError):
                # Corrupted cache file: don't fail the diagnosis over a
                # caching problem, but don't keep merging into broken
                # data either — start fresh.
                cache = {}

            cache[domain] = entry

            file_handle.seek(0)
            file_handle.truncate()
            json.dump(cache, file_handle, indent=2, ensure_ascii=False)
        finally:
            if _HAS_FCNTL:
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)

    return entry
