"""
Everything needed to reproduce one diagnosis, recorded with the result:
each dimension's METHOD_VERSION, the question set (category + version)
the buyer-question dimensions used, every page read with a hash of its
extracted text, and the run date.

Two results are only comparable score-for-score when their method
versions and question set version match (see
frontend/diagnose/src/utils/provenance.js); with matching page hashes
too, they were computed from the same input with the same method and
must be identical.
"""

from datetime import datetime, timezone

import source_layers
from engines._source_layers import LAYER_LIST_VERSION

from engines import (
    citation_engine,
    evidence_engine,
    fact_engine,
    recency_engine,
    relevance_engine,
    technical_engine,
)

METHOD_VERSIONS = {
    engine.DIMENSION_NAME: engine.METHOD_VERSION
    for engine in (
        citation_engine,
        relevance_engine,
        evidence_engine,
        recency_engine,
        fact_engine,
        technical_engine,
    )
}
# The layered diagnosis is part of the method too: results from before it
# existed are not directly comparable with results that have it.
METHOD_VERSIONS["source_layers"] = source_layers.METHOD_VERSION


def build_provenance(dimensions: list[dict]) -> dict:
    """`dimensions` is the list of DimensionScore dicts for one diagnosis."""
    question_set = None
    pages = {}
    for dimension in dimensions:
        details = dimension.get("details") or {}
        if dimension.get("dimension") == citation_engine.DIMENSION_NAME and details.get("question_set"):
            info = details["question_set"]
            question_set = {
                "category_id": info["category_id"],
                "category_name": info["category_name"],
                "version": info["version"],
                "status": info["status"],
            }
        if details.get("page_fingerprints"):
            pages[dimension["dimension"]] = details["page_fingerprints"]

    now = datetime.now(timezone.utc)
    return {
        "run_date": now.date().isoformat(),
        "run_at": now.isoformat(),
        "method_versions": dict(METHOD_VERSIONS),
        "source_layer_list_version": LAYER_LIST_VERSION,
        "question_set": question_set,
        "pages": pages,
    }
