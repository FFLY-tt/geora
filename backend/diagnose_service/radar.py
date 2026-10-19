"""
The five-corner chart of the result page, computed from the layered diagnosis
that source_layers.py already built. No new request and no model call.

Per corner (kind of website):
- gray: how many of every 100 sources AI used came from this kind of website
  (the layer's reliance x 100). The denominator is every source, including the
  kinds of website that have no corner (product_pages, reference).
- blue: the part of gray that mentions the company: gray x presence. It can
  never be bigger than gray. For the company's own website blue equals gray,
  because its own site always mentions it.
- status: "ok", "not_used" (AI never used this kind of website: gray and blue
  are 0) or "could_not_check" (AI used it but none of its pages could be
  opened: blue is 0 because nothing is known).

All five corners share one scale: the largest gray value rounded up to a
multiple of 10 (10 when everything is 0).

product_pages and reference have no corner. This is our judgment: competitors'
product pages are not somewhere a company can get itself mentioned, and
authoritative reference sites are rarely cited. Both stay in the details.
"""

import math

# Clockwise from the top, in the order the chart shows them.
RADAR_AXES = ["lists", "guides", "review_media", "community", "own_site"]


def _axis(layer: dict | None, key: str) -> dict:
    if layer is None or not layer.get("citations"):
        return {"key": key, "gray": 0.0, "blue": 0.0, "status": "not_used"}
    gray = round(layer["reliance"] * 100, 1)
    if key == "own_site":
        return {"key": key, "gray": gray, "blue": gray, "status": "ok"}
    if layer.get("presence") is None:
        return {"key": key, "gray": gray, "blue": 0.0, "status": "could_not_check"}
    return {"key": key, "gray": gray, "blue": round(gray * layer["presence"], 1), "status": "ok"}


def build_radar(source_layers: dict | None) -> dict | None:
    """The radar block for a result, or None when there is no layered diagnosis (AI cited no sources)."""
    if not source_layers:
        return None
    by_name = {layer["layer"]: layer for layer in source_layers.get("layers") or []}
    axes = [_axis(by_name.get(key), key) for key in RADAR_AXES]
    largest = max(axis["gray"] for axis in axes)
    scale_max = max(10, math.ceil(largest / 10) * 10)

    # The corner where the most is missing: the biggest gray minus blue among the corners
    # we could check. A tie goes to the corner that comes first. None when nothing is missing.
    biggest_key, biggest_value = None, 0.0
    for axis in axes:
        missing = round(axis["gray"] - axis["blue"], 1) if axis["status"] == "ok" else 0.0
        if missing > biggest_value:
            biggest_key, biggest_value = axis["key"], missing
    return {
        "scale_max": scale_max,
        "axes": axes,
        "biggest_gap": biggest_key,
        "total_sources": source_layers.get("total_citations"),
    }
