"""
Fingerprints of the pages an engine actually read, recorded in its
DimensionScore details under "page_fingerprints" so a result says
exactly which input it was computed from. Two results with the same
method versions, question set version, and page fingerprints were
computed from the same input with the same method, and must match.
"""

import hashlib


def page_fingerprint(url: str, text: str) -> dict:
    """{url, text_sha256, text_length} for the extracted text actually used (never raw HTML)."""
    return {
        "url": url,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_length": len(text),
    }
