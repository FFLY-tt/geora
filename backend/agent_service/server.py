"""
API entry point for the agent service (decision / content / monitor).

Runs as its own process, separate from diagnose_service, so new agent
work can be developed and deployed without touching the diagnostic tool
that is already working. Shares backend/shared/ (config, fetch utils,
report style guide, text format rules) with diagnose_service instead of
duplicating them.

/api/technical-package and /api/answer-capsules are deliberately two
separate routes instead of one combined endpoint: technical_package_engine
is pure rule-based generation with no external-API failure mode, while
answer_capsule_engine calls DeepSeek per question and can be slow or
fail. Splitting them means a bad day for the LLM-backed capsule work
never blocks the fast, always-available technical package, and the
frontend can show each its own loading state instead of one request
gating both.
"""

import asyncio
import sys
from pathlib import Path

# Make backend/shared importable regardless of where this process is
# launched from (this file lives in backend/agent_service/, shared/ is
# its sibling under backend/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from _cache_store import get_cached, save_cached
from answer_capsule_engine import generate_answer_capsules
from models import (
    AnswerCapsuleRequest,
    AnswerCapsuleResponse,
    TechnicalPackageRequest,
    TechnicalPackageResponse,
)
from technical_package_engine import generate_technical_package

TECHNICAL_PACKAGE_CACHE = "technical_package"
ANSWER_CAPSULE_CACHE = "answer_capsules"

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
    """Liveness check."""
    return {"status": "ok", "service": "agent"}


@app.post("/api/technical-package", response_model=TechnicalPackageResponse)
async def technical_package(request: TechnicalPackageRequest) -> TechnicalPackageResponse:
    """
    Pure rule-based JSON-LD, llms.txt, and robots.txt suggestions for a
    domain. No LLM call anywhere in this path, so this stays fast and
    has no AI-API failure mode. Cached per domain for 24 hours,
    independent of /api/answer-capsules's cache.
    """
    cached = await asyncio.to_thread(get_cached, TECHNICAL_PACKAGE_CACHE, request.domain)
    if cached:
        return TechnicalPackageResponse(**cached)

    package = await asyncio.to_thread(generate_technical_package, request.domain, request.company_name)
    result = {"domain": request.domain, "company_name": request.company_name, **package}
    cached_entry = await asyncio.to_thread(save_cached, TECHNICAL_PACKAGE_CACHE, request.domain, result)
    return TechnicalPackageResponse(**cached_entry)


@app.post("/api/answer-capsules", response_model=AnswerCapsuleResponse)
async def answer_capsules(request: AnswerCapsuleRequest) -> AnswerCapsuleResponse:
    """
    LLM-backed Answer Capsule generation: one self-contained answer
    paragraph per brand-free customer question, or confirmation the site
    already has one. Slower than /api/technical-package and has a real
    external-API failure mode, which is why it is a separate endpoint.
    A single question failing never fails the whole request — see
    answer_capsule_engine.generate_answer_capsules. Cached per domain
    for 24 hours, independent of /api/technical-package's cache.
    """
    cached = await asyncio.to_thread(get_cached, ANSWER_CAPSULE_CACHE, request.domain)
    if cached:
        return AnswerCapsuleResponse(**cached)

    capsule_result = await asyncio.to_thread(
        generate_answer_capsules, request.domain, request.company_name, request.questions
    )
    result = {"domain": request.domain, "company_name": request.company_name, **capsule_result}
    cached_entry = await asyncio.to_thread(save_cached, ANSWER_CAPSULE_CACHE, request.domain, result)
    return AnswerCapsuleResponse(**cached_entry)
