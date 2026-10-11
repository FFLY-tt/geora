"""
Local JSON-file cache for LLM judgment results, keyed by a hash of
(dimension/engine name, prompt version, model name, input content).

Why this exists: backend/scripts/stability_audit.py proved that when a
page's own content is byte-for-byte identical run to run, an LLM
judgment call on that exact same input still comes back with a
different raw output every time — pure model non-determinism, not a
measurement of anything real changing. For dimensions like
evidence_density and topic_relevance, the whole point of re-running a
diagnosis against the same domain is to see whether ITS CONTENT
changed, not to re-roll the same judgment and risk a different score
purely by chance. Caching the final, already-aggregated judgment
result per input means a repeated diagnosis against unchanged content
returns the exact same result every time, with no LLM call at all.

The input hashed into the key must always be the EXTRACTED, visible
page text, never the raw HTML — raw HTML can carry per-fetch noise
(CSP nonces, cache-busting query strings in asset URLs, etc.) that
changes on every single request even though the real visible content
a human or an AI reader would see did not.

No expiry: when the input content genuinely changes, its hash changes,
which is a different cache key — there is nothing stale to expire.
Each caller's prompt has its own version constant, bumped whenever
that prompt's wording changes in a way that could make a previously
cached judgment unrepresentative of the new prompt; that version is
folded into the key (see make_judgment_cache_key) so an old judgment
is never silently served as if it came from the new prompt.

Uses the same read-modify-write-with-file-lock pattern as
backend/diagnose_service/engines/_query_cache.py.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    # fcntl is POSIX-only. This project only targets macOS/Linux, so on
    # any other platform reads/writes simply fall back to unsynchronized.
    _HAS_FCNTL = False

_DATA_DIR = Path(__file__).parent.parent / "data"
_CACHE_FILE = _DATA_DIR / "judgment_cache.json"


def make_judgment_cache_key(namespace: str, prompt_version: str, model_name: str, input_content: str) -> str:
    """
    Build the cache key: sha256 of namespace + prompt version + model
    name + input content. `namespace` is normally the dimension name
    (e.g. "evidence_density") so two different engines never collide
    even on coincidentally identical input text. `input_content` must
    be the extracted, visible text actually judged — see module
    docstring for why never raw HTML. Bumping `prompt_version`
    whenever a prompt's wording changes automatically invalidates
    every judgment cached under the old wording, since the key itself
    changes.
    """
    raw = f"{namespace}\x1f{prompt_version}\x1f{model_name}\x1f{input_content}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_cache(file_handle) -> dict:
    """Read and parse the current contents of the cache file, if any."""
    file_handle.seek(0)
    raw_content = file_handle.read()
    if not raw_content.strip():
        return {}
    parsed = json.loads(raw_content)
    if not isinstance(parsed, dict):
        raise ValueError(f"Judgment cache file '{_CACHE_FILE}' does not contain a JSON object.")
    return parsed


def get_cached_judgment(cache_key: str) -> dict | None:
    """
    Return the cached judgment result dict for `cache_key`, or None on
    a cache miss. Any problem reading/parsing the cache (missing file,
    corrupted JSON, malformed entry) is treated as a miss rather than
    raised — a broken cache file should never block a diagnosis from
    running, it should just fall back to a fresh judgment.
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

    entry = cache.get(cache_key)
    if not isinstance(entry, dict) or "result" not in entry:
        return None
    return entry["result"]


def save_judgment(cache_key: str, result: dict) -> None:
    """
    Store `result` (the final, already-aggregated judgment — e.g. the
    median-scored evidence listing, or a question's majority
    classification) under `cache_key`, stamped with the current time.
    Creates backend/diagnose_service/data/ if it does not exist. Uses
    a read-modify-write cycle guarded by an exclusive file lock, same
    pattern as _query_cache.save_queries.

    Only ever call this with a real, successfully-aggregated result —
    never cache a failure or an "insufficient data" outcome, so a
    transient problem is retried on the next diagnosis instead of
    being served back forever.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

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

            cache[cache_key] = {
                "result": result,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }

            file_handle.seek(0)
            file_handle.truncate()
            json.dump(cache, file_handle, indent=2, ensure_ascii=False)
        finally:
            if _HAS_FCNTL:
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
