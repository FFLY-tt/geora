"""
Generic 24-hour JSON-file cache for agent_service, keyed by domain,
parametrized by which cache file to use. technical_package and
answer_capsules get separate physical files under agent_service/data/
so their data never mixes, but share this one read/write implementation
instead of duplicating it per endpoint.

Kept local to agent_service rather than in shared/: this is
agent_service's own runtime state, not something diagnose_service needs,
and the two services deliberately do not share a data/ directory (see
server.py) so a bug or data issue in one can never corrupt the other's
cache.

Uses the same read-modify-write-with-file-lock pattern as
diagnose_service/result_cache.py.
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

CACHE_TTL = timedelta(hours=24)


def _cache_file_path(cache_name: str) -> Path:
    return _DATA_DIR / f"{cache_name}.json"


def _read_cache(file_handle, cache_path: Path) -> dict:
    """Read and parse the current contents of the cache file, if any."""
    file_handle.seek(0)
    raw_content = file_handle.read()
    if not raw_content.strip():
        return {}
    parsed = json.loads(raw_content)
    if not isinstance(parsed, dict):
        raise ValueError(f"Cache file '{cache_path}' does not contain a JSON object.")
    return parsed


def get_cached(cache_name: str, domain: str) -> dict | None:
    """
    Return the cached entry for `domain` in the `cache_name` cache file
    if an entry younger than CACHE_TTL exists, else None.

    Any problem reading/parsing the cache (missing file, corrupted JSON,
    malformed entry) is treated as a cache miss rather than raised — a
    broken cache should never block a request from running.
    """
    cache_path = _cache_file_path(cache_name)
    try:
        with open(cache_path, "r", encoding="utf-8") as file_handle:
            if _HAS_FCNTL:
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_SH)
            try:
                cache = _read_cache(file_handle, cache_path)
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


def save_cached(cache_name: str, domain: str, result: dict) -> dict:
    """
    Store `result` for `domain` in the `cache_name` cache file, stamped
    with the current time, and return the stored entry (result plus
    `cached_at`) so the caller can return it immediately without a
    second read.

    Creates agent_service/data/ if it does not exist. Uses a
    read-modify-write cycle guarded by an exclusive file lock so
    concurrent requests cannot interleave writes and corrupt the file.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _cache_file_path(cache_name)

    entry = {**result, "cached_at": datetime.now(timezone.utc).isoformat()}

    with open(cache_path, "a+", encoding="utf-8") as file_handle:
        if _HAS_FCNTL:
            fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX)
        try:
            try:
                cache = _read_cache(file_handle, cache_path)
            except (ValueError, json.JSONDecodeError):
                # Corrupted cache file: don't fail the request over a
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
