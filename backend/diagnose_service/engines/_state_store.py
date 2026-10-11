"""
Shared, committed state for diagnose_service: the frozen buyer question
sets, the domain -> category assignments, and the LLM judgment cache.

Why this exists: the same input measured with the same method must give
the same result on any machine. Question sets and judgments used to
live in a per-machine cache directory (backend/diagnose_service/data/,
which is gitignored) and the question sets even expired after 7 days,
so switching machines or simply waiting a week silently changed the
test itself. Everything in STATE_DIR is committed to git instead, so
every machine that has pulled the repo runs the exact same test.

Every file is written as pretty-printed JSON with sorted keys and a
trailing newline, so a change shows up as a small, readable git diff.
Writes are a read-modify-write cycle guarded by a cross-platform lock
on a sidecar "<file>.lock" (gitignored), and the new content is written
to a temp file and swapped in with os.replace, so a crash mid-write can
never leave a half-written state file behind.
"""

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

try:
    import msvcrt
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False

STATE_DIR = Path(__file__).resolve().parent.parent / "state"

# How long to keep retrying for the Windows lock before giving up.
_LOCK_TIMEOUT_SECONDS = 30


@contextmanager
def _file_lock(path: Path):
    """Exclusive lock on `path`'s sidecar .lock file, released on exit. fcntl on POSIX, msvcrt on Windows."""
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as lock_handle:
        if _HAS_FCNTL:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        elif _HAS_MSVCRT:
            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    lock_handle.seek(0)
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.05)
        try:
            yield
        finally:
            if _HAS_FCNTL:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            elif _HAS_MSVCRT:
                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)


def _read_json_object(path: Path) -> dict:
    """Parse `path` as a JSON object; a missing or empty file is {}. Raises ValueError on a non-object or bad JSON."""
    try:
        raw_content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    if not raw_content.strip():
        return {}
    parsed = json.loads(raw_content)
    if not isinstance(parsed, dict):
        raise ValueError(f"State file '{path}' does not contain a JSON object.")
    return parsed


def dump_state_json(data: dict) -> str:
    """The one canonical serialization for every state file: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    """Write `content` to a temp file next to `path`, then os.replace it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as tmp_handle:
            tmp_handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_state(path: Path) -> dict:
    """
    Read a state file. Unlike the old local caches, a corrupted state
    file is NOT silently treated as empty: it is shared, committed data
    (most likely a bad merge), and quietly regenerating over it would
    replace frozen question sets with new ones. Raises ValueError /
    json.JSONDecodeError so the problem is visible.
    """
    return _read_json_object(path)


def update_state(path: Path, mutate) -> dict:
    """
    Read-modify-write `path` under the file lock. `mutate(data)` changes
    `data` in place (and may return a value, which is passed back to the
    caller). Returns whatever `mutate` returned.
    """
    with _file_lock(path):
        data = _read_json_object(path)
        result = mutate(data)
        _atomic_write(path, dump_state_json(data))
    return result
