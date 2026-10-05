"""
API entry point.

This module only wires HTTP routing to the diagnostic engines; it must not
contain any scoring or business logic of its own — that belongs in
`engines/`.
"""

import asyncio
import sys
from pathlib import Path

# Make backend/shared importable regardless of where this process is
# launched from (this file lives in backend/diagnose_service/, shared/
# is its sibling under backend/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from engines.citation_engine import check_citation_visibility
from engines.evidence_engine import check_evidence_density
from engines.fact_engine import check_fact_consistency
from engines.recency_engine import check_recency
from engines.relevance_engine import check_topic_relevance
from leads_store import save_lead
from models import DiagnosisRequest, DiagnosisResponse, LeadCaptureRequest
from result_cache import get_cached_result, get_previous_scores, save_result

app = FastAPI(title="Geora Diagnostic API")

# Allow local frontend dev servers to call this API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/diagnose", response_model=DiagnosisResponse)
async def diagnose(request: DiagnosisRequest) -> DiagnosisResponse:
    """
    Return a cached diagnosis for this domain if one was computed within
    the last 24 hours (result_cache.CACHE_TTL); otherwise run all 5
    diagnostic engines, cache the result, and return it.

    There is no parameter to force a recompute — see result_cache.py for
    why this is a deliberate product decision, not a missing feature.

    citation_visibility, evidence_density, content_recency and
    fact_consistency are all independent, so they run concurrently.
    topic_relevance reuses the 5 buyer questions citation_engine
    generated, so it runs after that result is available.
    """
    cached = get_cached_result(request.domain)
    if cached:
        return DiagnosisResponse(**cached)

    # Grab whatever result currently exists for this domain (even an
    # expired one) BEFORE it gets overwritten below, so the response can
    # show the user how scores moved since last time.
    previous_scores = await asyncio.to_thread(get_previous_scores, request.domain)

    citation_task = asyncio.to_thread(check_citation_visibility, request.company_name, request.domain)
    evidence_task = asyncio.to_thread(check_evidence_density, request.domain)
    recency_task = asyncio.to_thread(check_recency, request.domain)
    fact_task = asyncio.to_thread(check_fact_consistency, request.company_name, request.domain)

    citation_result, evidence_result, recency_result, fact_result = await asyncio.gather(
        citation_task, evidence_task, recency_task, fact_task
    )

    relevance_result = await asyncio.to_thread(check_topic_relevance, request.domain, citation_result)

    result = {
        "company_name": request.company_name,
        "domain": request.domain,
        "dimensions": [
            citation_result.model_dump(),
            relevance_result.model_dump(),
            evidence_result.model_dump(),
            recency_result.model_dump(),
            fact_result.model_dump(),
        ],
    }
    cached_entry = await asyncio.to_thread(save_result, request.domain, result)
    return DiagnosisResponse(**cached_entry, previous_scores=previous_scores)


@app.post("/api/capture-lead")
async def capture_lead(request: LeadCaptureRequest) -> dict:
    """
    Store a captured lead (email left by the user to receive their
    diagnosis report). Only validates the request and delegates to
    leads_store.save_lead; storage logic itself lives there.
    """
    await asyncio.to_thread(
        save_lead,
        request.email,
        request.company_name,
        request.domain,
        request.dimensions,
    )
    return {"status": "ok"}
