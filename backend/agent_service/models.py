"""Pydantic request/response models for agent_service's API."""

from pydantic import BaseModel, Field


class TechnicalPackageRequest(BaseModel):
    """Incoming request body for POST /api/technical-package."""

    domain: str = Field(..., description="Target company domain, e.g. 'example.com'.")
    company_name: str = Field(..., description="Target company name, e.g. 'Example Inc.'.")


class TechnicalPackageResponse(BaseModel):
    """Full response body for POST /api/technical-package."""

    domain: str = Field(..., description="Target company domain, echoed back from the request.")
    company_name: str = Field(..., description="Target company name, echoed back from the request.")
    json_ld: dict = Field(..., description="Generated Organization/SoftwareApplication JSON-LD, or an insufficient_data status.")
    llms_txt: dict = Field(..., description="Generated llms.txt content and metadata, or an insufficient_data status.")
    robots_txt: dict = Field(..., description="robots.txt suggestions for AI crawlers, or an insufficient_data status.")
    cached_at: str = Field(..., description="ISO 8601 timestamp of when this result was computed. Reused as-is for 24 hours per domain.")


class AnswerCapsuleRequest(BaseModel):
    """Incoming request body for POST /api/answer-capsules."""

    domain: str = Field(..., description="Target company domain, e.g. 'example.com'.")
    company_name: str = Field(..., description="Target company name, e.g. 'Example Inc.'.")
    questions: list[str] | None = Field(
        None,
        description="Optional brand-free customer questions already generated elsewhere (e.g. by a prior diagnosis run). If omitted, a fresh set is generated here.",
    )


class AnswerCapsuleResponse(BaseModel):
    """Full response body for POST /api/answer-capsules."""

    domain: str = Field(..., description="Target company domain, echoed back from the request.")
    company_name: str = Field(..., description="Target company name, echoed back from the request.")
    status: str = Field(..., description="'ok' if capsule generation ran, 'insufficient_data' if it could not start at all (e.g. homepage unreachable).")
    reason: str | None = Field(None, description="Present when status is 'insufficient_data'.")
    capsules: list[dict] = Field(default_factory=list, description="One capsule per question. A per-question failure is marked generation_failed rather than dropped.")
    cached_at: str = Field(..., description="ISO 8601 timestamp of when this result was computed. Reused as-is for 24 hours per domain.")


class DecideDimensionScore(BaseModel):
    """
    One dimension's result from a prior diagnosis, as input to Decide.

    Mirrors diagnose_service's own DimensionScore shape, redefined here
    rather than imported so agent_service has no dependency on
    diagnose_service internals.
    """

    dimension: str = Field(..., description="Name of the diagnostic dimension, e.g. 'citation_visibility'.")
    score: int = Field(..., description="Score from 0 to 100, or -1 if the score could not be determined.")
    details: dict = Field(default_factory=dict, description="Raw evidence and reasoning backing the score.")


class DecideRequest(BaseModel):
    """Incoming request body for POST /api/decide."""

    domain: str = Field(..., description="Target company domain, e.g. 'example.com'.")
    company_name: str = Field(..., description="Target company name, e.g. 'Example Inc.'.")
    dimensions: list[DecideDimensionScore] = Field(..., description="The dimensions list from a prior POST /api/diagnose result.")


class DecideResponse(BaseModel):
    """Full response body for POST /api/decide."""

    domain: str = Field(..., description="Target company domain, echoed back from the request.")
    company_name: str = Field(..., description="Target company name, echoed back from the request.")
    outcome: dict | None = Field(None, description="The citation_visibility dimension, reported as an outcome and never ranked. Null if not present in the input.")
    this_week: list[dict] = Field(default_factory=list, description="Top 3 ranked action items.")
    all_ranked: list[dict] = Field(default_factory=list, description="All reason dimensions that had a real score, ranked by priority.")
    needs_data: list[dict] = Field(default_factory=list, description="Reason dimensions with score -1: no real data was available, so they are never ranked or guessed at.")
    warnings: list[str] = Field(default_factory=list, description="Notes about unrecognized dimension names that were ignored.")
    method: dict = Field(..., description="The priority formula and the weight/weight_basis used for each reason dimension, for transparency.")
