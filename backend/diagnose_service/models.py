"""
Shared Pydantic data models used across the API layer and the engines.

Keeping these in one place ensures every engine returns a result shaped
exactly the way the API contract expects.
"""

from pydantic import BaseModel, Field


class DiagnosisRequest(BaseModel):
    """Incoming request body for POST /api/diagnose."""

    domain: str = Field(..., description="Target company domain, e.g. 'example.com'.")
    company_name: str = Field(..., description="Target company name, e.g. 'Example Inc.'.")


class DimensionScore(BaseModel):
    """
    Result of a single diagnostic dimension (one engine).

    `score` must be an integer from 0 to 100, or -1 when the engine could
    not obtain enough real data to compute a meaningful score. In that
    case `details` must explain why (see each engine's docstring) instead
    of a fabricated number.
    """

    dimension: str = Field(..., description="Name of the diagnostic dimension, e.g. 'citation_visibility'.")
    score: int = Field(..., description="Score from 0 to 100, or -1 if the score could not be determined.")
    details: dict = Field(default_factory=dict, description="Raw evidence and reasoning backing the score.")


class DiagnosisResponse(BaseModel):
    """Full response body for POST /api/diagnose."""

    company_name: str = Field(..., description="Target company name, echoed back from the request.")
    domain: str = Field(..., description="Target company domain, echoed back from the request.")
    dimensions: list[DimensionScore] = Field(..., description="One DimensionScore per diagnostic engine.")
    cached_at: str = Field(
        ...,
        description="ISO 8601 timestamp of when this result was computed. Results for a domain are reused as-is for 24 hours; there is no way to force a recompute.",
    )
    previous_scores: dict[str, int] | None = Field(
        None,
        description="Map of dimension name to score from the last time this domain was checked, before this run. Only populated when this response is a fresh recompute (not a cache hit) and a prior result exists; null otherwise.",
    )


class LeadCaptureRequest(BaseModel):
    """Incoming request body for POST /api/capture-lead."""

    email: str = Field(..., description="Email address left by the user to receive the full report.")
    company_name: str = Field(..., description="Target company name from the diagnosis the user viewed.")
    domain: str = Field(..., description="Target company domain from the diagnosis the user viewed.")
    dimensions: list[DimensionScore] = Field(..., description="Diagnosis dimensions the user was viewing when they left their email.")
