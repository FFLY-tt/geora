"""
Local JSON-file cache for citation_engine's generated (category, buyer
questions) pair, keyed by domain.

Why this exists: regenerating the 5 buyer questions on every diagnosis
run made citation_visibility (and, downstream, topic_relevance, which
reuses citation_engine's Perplexity answers) swing wildly between runs
for the same domain. The actual wording of the questions changes the
Perplexity search results and answers, even at a low DeepSeek
temperature — the instability was never really about randomness in
DeepSeek's judgment, it was that each run was a genuinely different test.
Caching the generated pair per domain for a period keeps repeated runs
against the same domain comparable, the way a real measurement
instrument reuses the same test procedure rather than reinventing it
each time.

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

_DATA_DIR = Path(__file__).parent.parent / "data"
_CACHE_FILE = _DATA_DIR / "query_cache.json"

CACHE_TTL = timedelta(days=7)


def _read_cache(file_handle) -> dict:
    """Read and parse the current contents of the cache file, if any."""
    file_handle.seek(0)
    raw_content = file_handle.read()
    if not raw_content.strip():
        return {}
    parsed = json.loads(raw_content)
    if not isinstance(parsed, dict):
        raise ValueError(f"Query cache file '{_CACHE_FILE}' does not contain a JSON object.")
    return parsed


def get_cached_queries(domain: str) -> tuple[str, list[str]] | None:
    """
    Return (category, queries) cached for `domain` if a fresh (younger
    than CACHE_TTL) entry exists, else None.

    Any problem reading/parsing the cache (missing file, corrupted JSON,
    malformed entry) is treated as a cache miss rather than raised — a
    broken cache should never block a diagnosis from running, it should
    just fall back to regenerating.
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

    category = entry.get("category")
    queries = entry.get("queries")
    generated_at_raw = entry.get("generated_at")

    if not category or not isinstance(queries, list) or not queries or not generated_at_raw:
        return None

    try:
        generated_at = datetime.fromisoformat(generated_at_raw)
    except ValueError:
        return None

    if datetime.now(timezone.utc) - generated_at > CACHE_TTL:
        return None

    return category, queries


def save_queries(domain: str, category: str, queries: list[str]) -> None:
    """
    Store the generated (category, queries) pair for `domain`, stamped
    with the current time. Creates backend/data/ if it does not exist.
    Uses a read-modify-write cycle guarded by an exclusive file lock so
    concurrent requests cannot interleave writes and corrupt the file.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    with open(_CACHE_FILE, "a+", encoding="utf-8") as file_handle:
        if _HAS_FCNTL:
            fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX)
        try:
            try:
                cache = _read_cache(file_handle)
            except (ValueError, json.JSONDecodeError):
                # Corrupted cache file: don't propagate the error and fail
                # the whole diagnosis over a caching problem, but also
                # don't silently keep merging into broken data — start fresh.
                cache = {}

            cache[domain] = {
                "category": category,
                "queries": queries,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

            file_handle.seek(0)
            file_handle.truncate()
            json.dump(cache, file_handle, indent=2, ensure_ascii=False)
        finally:
            if _HAS_FCNTL:
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
