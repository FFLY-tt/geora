"""
API entry point.

This module only wires HTTP routing to the diagnostic engines; it must not
contain any scoring or business logic of its own — that belongs in
`engines/`.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Make backend/shared importable regardless of where this process is
# launched from (this file lives in backend/diagnose_service/, shared/
# is its sibling under backend/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from competitors import CompetitorInputError, compare_competitors, parse_competitors, signature
from engines.citation_engine import check_citation_visibility
from engines.evidence_engine import check_evidence_density
from engines.fact_engine import check_fact_consistency
from engines.recency_engine import check_recency
from engines.relevance_engine import check_topic_relevance
from engines.technical_engine import check_technical_readability
from leads_store import save_lead
from models import DiagnosisRequest, DiagnosisResponse, LeadCaptureRequest
from provenance import build_provenance
from radar import build_radar
from source_layers import build_source_layers
from result_cache import (
    get_cached_result,
    get_previous_citation_interval,
    get_previous_provenance,
    get_previous_scores,
    save_result,
    update_cached_competitors,
)

logger = logging.getLogger(__name__)

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

    citation_visibility, evidence_density, content_recency,
    fact_consistency, and technical_readability are all independent, so
    they run concurrently. topic_relevance reuses the 5 buyer questions
    citation_engine generated, so it runs after that result is
    available.
    """
    try:
        competitors = parse_competitors(request.competitors, request.company_name, request.domain)
    except CompetitorInputError as error:
        raise HTTPException(status_code=422, detail=str(error))

    cached = get_cached_result(request.domain)
    if cached:
        if "radar" not in cached and cached.get("source_layers"):
            # Results saved before the chart existed: the chart is made from the stored layers.
            cached = {**cached, "radar": build_radar(cached["source_layers"])}
        if signature(cached.get("competitors")) != signature(competitors):
            # A different competitor list: recount from the answers stored with the cached
            # result. No new diagnosis and no Perplexity call.
            comparison = await asyncio.to_thread(_comparison, request, competitors, cached)
            updated = await asyncio.to_thread(update_cached_competitors, request.domain, competitors, comparison)
            cached = updated or {**cached, "competitors": competitors or None, "competitor_comparison": comparison}
        return DiagnosisResponse(**cached)

    # Grab whatever result currently exists for this domain (even an
    # expired one) BEFORE it gets overwritten below, so the response can
    # show the user how scores moved since last time.
    previous_scores = await asyncio.to_thread(get_previous_scores, request.domain)
    previous_provenance = await asyncio.to_thread(get_previous_provenance, request.domain)
    previous_citation_interval = await asyncio.to_thread(get_previous_citation_interval, request.domain)

    citation_task = asyncio.to_thread(check_citation_visibility, request.company_name, request.domain)
    evidence_task = asyncio.to_thread(check_evidence_density, request.domain)
    recency_task = asyncio.to_thread(check_recency, request.domain)
    fact_task = asyncio.to_thread(check_fact_consistency, request.company_name, request.domain)
    technical_task = asyncio.to_thread(check_technical_readability, request.domain)

    citation_result, evidence_result, recency_result, fact_result, technical_result = await asyncio.gather(
        citation_task, evidence_task, recency_task, fact_task, technical_task
    )

    relevance_result = await asyncio.to_thread(check_topic_relevance, request.domain, citation_result)

    dimensions = [
        citation_result.model_dump(),
        relevance_result.model_dump(),
        evidence_result.model_dump(),
        recency_result.model_dump(),
        fact_result.model_dump(),
        technical_result.model_dump(),
    ]
    # The layered view is built from citations already collected; it must
    # never take the diagnosis down, so a failure just leaves it out.
    try:
        layers = await asyncio.to_thread(
            build_source_layers, request.company_name, request.domain, citation_result.details
        )
    except Exception:
        logger.exception("Source layer analysis failed for %s", request.domain)
        layers = None

    result = {
        "company_name": request.company_name,
        "domain": request.domain,
        "dimensions": dimensions,
        "source_layers": layers,
        "radar": build_radar(layers),
        "competitors": competitors or None,
        "competitor_comparison": await asyncio.to_thread(_comparison, request, competitors, {"dimensions": dimensions}),
        "provenance": build_provenance(dimensions),
    }
    cached_entry = await asyncio.to_thread(save_result, request.domain, result)
    return DiagnosisResponse(
        **cached_entry,
        previous_scores=previous_scores,
        previous_provenance=previous_provenance,
        previous_citation_interval=previous_citation_interval,
    )


def _comparison(request: DiagnosisRequest, competitors: list[dict], result: dict) -> dict | None:
    """The competitor comparison for `result`'s stored citation answers; never takes the diagnosis down."""
    if not competitors:
        return None
    citation = next((d for d in result.get("dimensions") or [] if d.get("dimension") == "citation_visibility"), None)
    if not citation:
        return None
    try:
        return compare_competitors(request.company_name, request.domain, competitors, citation.get("details") or {})
    except Exception:
        logger.exception("Competitor comparison failed for %s", request.domain)
        return None


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
