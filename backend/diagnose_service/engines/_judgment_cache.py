"""
Shared, committed cache for LLM judgment results, keyed by a hash of
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

Where it lives: state/judgments/<namespace>.json (one file per engine),
committed to git — see engines._state_store for why this is shared
state and not a per-machine cache. A judgment made on one machine is
reused on every other machine after a git pull, so the same page text
gets the same score everywhere. Entries are stored with sorted keys so
a new judgment shows up as a clean git diff.

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
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from engines import _state_store


def _judgments_dir() -> Path:
    return _state_store.STATE_DIR / "judgments"


def _judgment_file(namespace: str) -> Path:
    """state/judgments/<namespace>.json; the namespace is sanitized to a safe file name."""
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", namespace)
    return _judgments_dir() / f"{safe_name}.json"


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


def get_cached_judgment(namespace: str, cache_key: str) -> dict | None:
    """
    Return the cached judgment result dict for `cache_key` in
    `namespace`'s file, or None on a miss. A missing or unreadable file
    is treated as a miss rather than raised — it should never block a
    diagnosis from running.
    """
    try:
        cache = _state_store.read_state(_judgment_file(namespace))
    except (ValueError, json.JSONDecodeError, OSError):
        return None

    entry = cache.get(cache_key)
    if not isinstance(entry, dict) or "result" not in entry:
        return None
    return entry["result"]


def save_judgment(namespace: str, cache_key: str, result: dict) -> None:
    """
    Store `result` (the final, already-aggregated judgment — e.g. the
    union of verified evidence items, or a question's majority
    classification) under `cache_key` in `namespace`'s file, stamped
    with the current time.

    An existing entry is never overwritten: the first judgment stored
    for an input is THE judgment for that input, on every machine.

    Only ever call this with a real, successfully-aggregated result —
    never cache a failure or an "insufficient data" outcome, so a
    transient problem is retried on the next diagnosis instead of
    being served back forever.

    If the file is corrupted (e.g. a bad git merge), nothing is written:
    the diagnosis still completes, and the broken file is left for a
    human to fix rather than being silently replaced.
    """

    def _mutate(cache: dict) -> None:
        if cache_key in cache:
            return
        cache[cache_key] = {
            "result": result,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        _state_store.update_state(_judgment_file(namespace), _mutate)
    except (ValueError, json.JSONDecodeError):
        return
