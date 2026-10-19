"""
The bars of the result page "Where AI looked for answers", computed from the layered
diagnosis source_layers.py already built. No new request and no model call.

Per kind of website AI read (the five kinds with a bar), counted by PAGE, not by how often
AI used it: the same page used in many answers is one page. A page address is put in one
format first (normalize_citation_url), then repeats are merged.

- pages: pages we opened. They are what the bar is drawn from.
- mention_pages: the opened pages that mention the company (the rule is the one the layered
  diagnosis uses). The rest of the opened pages are the gray part of the bar.
- could_not_open: pages we tried and could not open, bot checks included. Not drawn.
- not_checked: pages we did not try, because only the most used pages are opened.
  Not drawn either.

The company's own website is all blue: its own pages mention it. A kind with no page at all
has no row. product_pages and reference have no bar. This is our judgment: competitors'
product pages are not somewhere a company can get itself mentioned, and reference sites are
rarely used. Both stay in the details.
"""

from source_layers import normalize_citation_url

# Display name per kind, in the order used to break ties.
BAR_KINDS = [
    ("lists", "Best tools lists"),
    ("guides", "Guides and articles"),
    ("review_media", "Review sites and news"),
    ("community", "Community discussions"),
    ("own_site", "Your own website"),
]

_STATUS_RANK = {"checked": 0, "cited": 1, "unknown": 2, "not_checked": 3}


def _unique_pages(pages: list[dict]) -> list[dict]:
    """The pages of one kind, one per normalized address. The best known status wins; one mention is enough."""
    merged: dict[str, dict] = {}
    for page in pages:
        key = normalize_citation_url(page.get("url") or "") or (page.get("url") or "")
        seen = merged.get(key)
        if seen is None:
            merged[key] = dict(page)
            continue
        if _STATUS_RANK.get(page.get("status"), 9) < _STATUS_RANK.get(seen.get("status"), 9):
            seen["status"] = page["status"]
        seen["mentions_you"] = bool(seen.get("mentions_you") or page.get("mentions_you"))
    return list(merged.values())


def _row(key: str, name: str, pages: list[dict]) -> dict:
    unique = _unique_pages(pages)
    if key == "own_site":
        return {"key": key, "name": name, "pages": len(unique), "mention_pages": len(unique), "could_not_open": 0, "not_checked": 0}
    opened = [p for p in unique if p.get("status") == "checked"]
    return {
        "key": key,
        "name": name,
        "pages": len(opened),
        "mention_pages": sum(1 for p in opened if p.get("mentions_you")),
        "could_not_open": sum(1 for p in unique if p.get("status") == "unknown"),
        "not_checked": sum(1 for p in unique if p.get("status") == "not_checked"),
    }


def build_source_bars(source_layers: dict | None) -> dict | None:
    """
    {rows, max_pages, most_missing} or None when there is no layered diagnosis (AI used no
    sources) or no kind has any page. Rows are sorted by pages, most first, and a tie keeps
    BAR_KINDS order. `most_missing` is the kind (never the company's own website) with the most
    opened pages that do not mention the company, or None when there is none.
    """
    if not source_layers:
        return None
    by_name = {layer["layer"]: layer for layer in source_layers.get("layers") or []}
    rows = []
    for key, name in BAR_KINDS:
        row = _row(key, name, (by_name.get(key) or {}).get("pages") or [])
        if row["pages"] or row["could_not_open"] or row["not_checked"]:
            rows.append(row)
    if not rows:
        return None
    order = {key: index for index, (key, _name) in enumerate(BAR_KINDS)}
    rows.sort(key=lambda row: (-row["pages"], order[row["key"]]))

    most_missing, most = None, 0
    for row in sorted(rows, key=lambda row: order[row["key"]]):
        missing = row["pages"] - row["mention_pages"]
        if row["key"] != "own_site" and missing > most:
            most_missing, most = row["key"], missing
    return {"rows": rows, "max_pages": max(1, max(row["pages"] for row in rows)), "most_missing": most_missing}
