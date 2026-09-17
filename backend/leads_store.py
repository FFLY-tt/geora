"""
Local JSON-file storage for captured leads (emails left by users to
receive their diagnosis report).

This is deliberately file-based rather than backed by a database: lead
volume is currently low enough that a single JSON file is sufficient, as
long as concurrent writes are serialized safely with a file lock.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    # fcntl is POSIX-only. This project only targets macOS/Linux, so on
    # any other platform writes simply fall back to being unsynchronized.
    _HAS_FCNTL = False

_DATA_DIR = Path(__file__).parent / "data"
_LEADS_FILE = _DATA_DIR / "leads.json"


def _to_serializable(dimensions: list) -> list:
    """
    Convert a list of dimension entries into plain JSON-serializable
    dicts, whether each entry is already a dict or a Pydantic model
    (e.g. DimensionScore) with a `model_dump` method.
    """
    serialized = []
    for entry in dimensions:
        if hasattr(entry, "model_dump"):
            serialized.append(entry.model_dump())
        else:
            serialized.append(entry)
    return serialized


def _read_existing_leads(file_handle) -> list:
    """Read and parse the current contents of the leads file, if any."""
    file_handle.seek(0)
    raw_content = file_handle.read()
    if not raw_content.strip():
        return []
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        # The file exists but is not valid JSON (e.g. corrupted by a
        # previous crash mid-write). Fail loudly instead of silently
        # discarding existing leads by overwriting with an empty list.
        raise ValueError(f"Leads file '{_LEADS_FILE}' contains invalid JSON and cannot be safely appended to.") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"Leads file '{_LEADS_FILE}' does not contain a JSON array.")
    return parsed


def save_lead(email: str, company_name: str, domain: str, dimensions: list) -> None:
    """
    Append one lead record to backend/data/leads.json.

    Creates backend/data/ if it does not exist yet. Uses a read-modify-write
    cycle guarded by an exclusive file lock (fcntl.flock) so concurrent
    requests cannot interleave writes and corrupt the JSON array.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Open in "a+": creates the file if missing, without truncating any
    # existing content, while still allowing explicit seek/read/write below.
    with open(_LEADS_FILE, "a+", encoding="utf-8") as file_handle:
        if _HAS_FCNTL:
            fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX)
        try:
            leads = _read_existing_leads(file_handle)

            leads.append({
                "email": email,
                "company_name": company_name,
                "domain": domain,
                "dimensions": _to_serializable(dimensions),
                "captured_at": datetime.now(timezone.utc).isoformat(),
            })

            file_handle.seek(0)
            file_handle.truncate()
            json.dump(leads, file_handle, indent=2, ensure_ascii=False)
        finally:
            if _HAS_FCNTL:
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
