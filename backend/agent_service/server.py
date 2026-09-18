"""
API entry point for the agent service (decision / content / monitor).

Runs as its own process, separate from diagnose_service, so new agent
work can be developed and deployed without touching the diagnostic tool
that is already working. Shares backend/shared/ (config, fetch utils,
report style guide, text format rules) with diagnose_service instead of
duplicating them.

Empty scaffold for now: P0 (technical layer package) and the decision
engine's ranking logic land here next.
"""

import sys
from pathlib import Path

# Make backend/shared importable regardless of where this process is
# launched from (this file lives in backend/agent_service/, shared/ is
# its sibling under backend/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.config import settings  # noqa: F401  (import-smoke-tests config wiring)

app = FastAPI(title="Geora Agent API")

# Allow local frontend dev servers to call this API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    """Liveness check while this service is still an empty scaffold."""
    return {"status": "ok", "service": "agent"}
