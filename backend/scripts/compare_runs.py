"""
Compare two diagnosis runs of the same company: the mention rate with its
likely range, the source group with the biggest gap, and whether the change in
mention rate is bigger than the margin of error.

Each input is a saved JSON diagnosis: the body returned by POST /api/diagnose,
or the result cache file (backend/diagnose_service/data/result_cache.json,
which holds one such entry per domain; then pass --domain).

Usage:
    python backend/scripts/compare_runs.py earlier.json later.json
    python backend/scripts/compare_runs.py cache_copy_monday.json cache_copy_tuesday.json --domain close.com

The mention rate is the share of the 15 answers that mention the company. With
so few answers it moves by chance, so a change only counts as real when the two
likely ranges (Wilson 95% intervals) do not overlap. Runs made with a different
method or question set are not directly comparable, and the report says so.

Exit status: 0 when the report was produced, 2 when the files can't be compared
(different companies, or a file without a mention rate).
"""

import argparse
import json
import sys
from pathlib import Path

_SERVICE_DIR = Path(__file__).resolve().parent.parent / "diagnose_service"
sys.path.insert(0, str(_SERVICE_DIR))
sys.path.insert(0, str(_SERVICE_DIR.parent))

from engines._stats import wilson_interval  # noqa: E402


class CompareError(Exception):
    """The two files can't be compared; the message says why."""


def load_run(path: str, domain: str | None = None) -> dict:
    """The diagnosis stored in `path` (a response/cache entry, or a cache file holding entries by domain)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompareError(f"Can't read {path}: {error}")
    if not isinstance(data, dict):
        raise CompareError(f"{path} doesn't hold a diagnosis")
    if "dimensions" in data:
        return data
    if domain and isinstance(data.get(domain), dict):
        return data[domain]
    if domain:
        raise CompareError(f"{path} has no entry for {domain}")
    domains = [key for key, value in data.items() if isinstance(value, dict) and "dimensions" in value]
    if len(domains) == 1:
        return data[domains[0]]
    raise CompareError(f"{path} holds several companies ({', '.join(domains) or 'none'}); pass --domain")


def summarize(run: dict, label: str) -> dict:
    """The numbers the comparison uses, taken from one diagnosis."""
    citation = next((d for d in run.get("dimensions") or [] if d.get("dimension") == "citation_visibility"), None)
    details = (citation or {}).get("details") or {}
    mentions, total = details.get("mentions"), details.get("total")
    if citation is None or citation.get("score") == -1 or not total or mentions is None:
        raise CompareError(f"{label} has no mention rate (the check did not get enough answers)")

    low, high = details.get("ci_low"), details.get("ci_high")
    if low is None or high is None:
        low, high = wilson_interval(mentions, total)

    layers = run.get("source_layers") or {}
    biggest = layers.get("biggest_gap_layer")
    gap = next((l.get("gap") for l in layers.get("layers") or [] if l.get("layer") == biggest), None)
    provenance = run.get("provenance") or {}
    return {
        "label": label,
        "company": run.get("company_name"),
        "domain": run.get("domain"),
        "when": run.get("cached_at") or (provenance.get("run_date")),
        "mentions": mentions,
        "total": total,
        "rate": mentions / total,
        "low": low,
        "high": high,
        "biggest_gap_layer": biggest,
        "biggest_gap": gap,
        "conclusion": (layers.get("conclusion") or [None])[0],
        "citation_method": (provenance.get("method_versions") or {}).get("citation_visibility"),
        "question_set": provenance.get("question_set"),
    }


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def _describe(summary: dict) -> list[str]:
    lines = [
        f"{summary['label']} ({summary['when'] or 'time unknown'})",
        f"  Mentioned in {summary['mentions']} of {summary['total']} answers ({_pct(summary['rate'])}); "
        f"likely range {_pct(summary['low'])}–{_pct(summary['high'])}",
    ]
    if summary["biggest_gap_layer"]:
        gap = "" if summary["biggest_gap"] is None else f" (gap {_pct(summary['biggest_gap'])})"
        lines.append(f"  Biggest gap: {summary['biggest_gap_layer']}{gap}")
    else:
        lines.append("  Biggest gap: none found")
    if summary["conclusion"]:
        lines.append(f"  Top conclusion: {summary['conclusion']}")
    return lines


def intervals_overlap(a: dict, b: dict) -> bool:
    """True when the two likely ranges overlap (touching counts as overlapping)."""
    return a["low"] <= b["high"] and b["low"] <= a["high"]


def compare(earlier: dict, later: dict) -> dict:
    """Facts about the change from `earlier` to `later`."""
    if (earlier["domain"] or "").lower() != (later["domain"] or "").lower():
        raise CompareError(f"These are different companies ({earlier['domain']} and {later['domain']})")
    same_method = earlier["citation_method"] == later["citation_method"] and earlier["question_set"] == later["question_set"]
    within_margin = intervals_overlap(earlier, later)
    if later["mentions"] * earlier["total"] == earlier["mentions"] * later["total"]:
        direction = "same"
    else:
        direction = "up" if later["rate"] > earlier["rate"] else "down"
    return {
        "comparable": same_method,
        "within_margin": within_margin,
        "direction": direction,
        "same_biggest_gap": earlier["biggest_gap_layer"] == later["biggest_gap_layer"],
    }


def report(earlier: dict, later: dict) -> str:
    outcome = compare(earlier, later)
    lines = [f"{later['company'] or later['domain']} ({later['domain']})", ""]
    lines += _describe(earlier) + [""] + _describe(later) + [""]

    change = f"{_pct(earlier['rate'])} → {_pct(later['rate'])}"
    if not outcome["comparable"]:
        lines.append(
            "Not directly comparable: the two runs used a different method or question set, so a difference "
            "in the numbers may come from that and not from the company."
        )
    if outcome["direction"] == "same":
        lines.append(f"Mention rate: unchanged ({change}).")
    elif outcome["within_margin"]:
        lines.append(
            f"Mention rate: {change}. This change is within the margin of error: the two likely ranges "
            "overlap, so it can't be told apart from chance."
        )
    else:
        word = "rose" if outcome["direction"] == "up" else "fell"
        lines.append(
            f"Mention rate: {change}. The mention rate {word} by more than the margin of error: "
            "the two likely ranges don't overlap."
        )

    if earlier["biggest_gap_layer"] is None and later["biggest_gap_layer"] is None:
        lines.append("Biggest gap: none found in either run.")
    elif outcome["same_biggest_gap"]:
        lines.append(f"Biggest gap: still {later['biggest_gap_layer']}.")
    else:
        lines.append(f"Biggest gap: moved from {earlier['biggest_gap_layer']} to {later['biggest_gap_layer']}.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two diagnosis runs of the same company.")
    parser.add_argument("earlier", help="JSON of the earlier run")
    parser.add_argument("later", help="JSON of the later run")
    parser.add_argument("--domain", help="Which company to take when a file holds several (a result cache file)")
    args = parser.parse_args(argv)
    try:
        earlier = summarize(load_run(args.earlier, args.domain), "Earlier run")
        later = summarize(load_run(args.later, args.domain), "Later run")
        print(report(earlier, later))
    except CompareError as error:
        print(f"Can't compare: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
