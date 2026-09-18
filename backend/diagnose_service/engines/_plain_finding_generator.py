"""
Shared AI-authored `plain_finding` generator, used by all 5 engines.

Design: each engine computes its real, verified facts exactly as before
(scores, counts, comparisons — nothing here changes how those are
computed). Instead of each engine hand-writing English prose around
those facts, the facts are handed to DeepSeek as a plain-text summary,
and DeepSeek writes the founder-facing report from them, under the
shared style rules in engines._report_style_guide. The model may only
phrase the given facts — it is never asked to introduce facts of its
own, so this does not weaken the project's no-fabrication rule, it just
moves who writes the final sentences.

plain_finding is a dict with exactly three keys: what_we_found,
what_it_costs_you, fix_it_today. Each is pure sentence content with NO
label or heading inside it — the frontend owns rendering the labels
("What we found:", etc.), so only one side of the stack ever adds them.
This is enforced structurally (the model returns JSON fields, not
labeled prose) rather than by prompt wording alone, because prompt
wording alone already proved unreliable: an earlier version asked for
"three labeled parts" in free text and the model dutifully included the
labels themselves, duplicating whatever the frontend also rendered.

The output is validated and retried, and a deterministic fallback is
used if AI generation isn't available or doesn't cooperate, so a user
is never shown a raw, non-compliant, or missing plain_finding.
"""

import json
import re

import requests

from shared.config import settings
from shared._report_style_guide import PLAIN_LANGUAGE_SYSTEM_PROMPT
from shared._text_format import find_report_violations, sanitize_banned_formatting, strip_leaked_label

MAX_ATTEMPTS = 2

PLAIN_FINDING_FIELDS = ("what_we_found", "what_it_costs_you", "fix_it_today")


def _call_deepseek_for_plain_finding(facts_summary: str) -> dict:
    """
    Ask DeepSeek to write the plain_finding report from `facts_summary`,
    as a JSON object with PLAIN_FINDING_FIELDS. Raises
    requests.RequestException on network/HTTP failure, ValueError if the
    response isn't valid JSON in the expected shape, and
    KeyError/IndexError if the response shape is otherwise unexpected.
    """
    url = f"{settings.deepseek_api_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.http_user_agent,
    }
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": PLAIN_LANGUAGE_SYSTEM_PROMPT},
            {"role": "user", "content": facts_summary},
        ],
        "temperature": 0.2,
    }
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=settings.request_timeout_seconds,
    )
    response.raise_for_status()
    raw_response = response.json()
    content = raw_response["choices"][0]["message"]["content"]

    # Models occasionally wrap JSON in a code fence despite instructions;
    # strip that defensively before parsing.
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    parsed = json.loads(cleaned)

    if not all(field in parsed for field in PLAIN_FINDING_FIELDS):
        raise ValueError(f"DeepSeek response missing expected fields: {parsed!r}")

    return parsed


def make_plain_finding(facts_summary: str, fallback_text: str) -> tuple[dict, list]:
    """
    Generate a founder-facing plain_finding dict from `facts_summary` (a
    plain-text block of real, already-computed facts — never fabricated
    by the model).

    Retries once if any field contains banned formatting, a leaked
    vendor/model name, a citation marker, or a leaked field label. If
    still not clean after that, every field is deterministically
    sanitized rather than shown as non-compliant. If DEEPSEEK_API_KEY
    isn't configured, or the API call itself fails after retries,
    `fallback_text` (a caller-supplied, already-clean, deterministic
    report built the old way) is used as the "what_we_found" value with
    the other two fields left empty, so a user is never left with no
    report at all, and the frontend simply skips rendering empty parts.

    Returns (plain_finding, generation_log) where generation_log records
    what happened on each attempt, kept for debugging.
    """
    fallback = {"what_we_found": fallback_text, "what_it_costs_you": "", "fix_it_today": ""}

    if not settings.has_deepseek_key():
        return fallback, [{"attempt": "skipped", "reason": "DEEPSEEK_API_KEY not configured, used fallback"}]

    generation_log = []
    last_parsed = None

    for attempt_number in range(1, MAX_ATTEMPTS + 1):
        try:
            parsed = _call_deepseek_for_plain_finding(facts_summary)
        except (requests.RequestException, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
            generation_log.append({"attempt": attempt_number, "issues": [f"generation failed: {exc}"]})
            continue

        last_parsed = parsed
        violations = []
        for field in PLAIN_FINDING_FIELDS:
            violations.extend(find_report_violations(str(parsed.get(field, ""))))

        if not violations:
            generation_log.append({"attempt": attempt_number, "issues": []})
            return {field: parsed[field] for field in PLAIN_FINDING_FIELDS}, generation_log

        generation_log.append({"attempt": attempt_number, "issues": violations})

    if last_parsed is not None:
        sanitized = {}
        for field in PLAIN_FINDING_FIELDS:
            value = strip_leaked_label(str(last_parsed.get(field, "")))
            sanitized[field] = sanitize_banned_formatting(value)
        generation_log.append({"attempt": "sanitized", "issues": []})
        return sanitized, generation_log

    generation_log.append({"attempt": "fallback", "reason": "All AI generation attempts failed, used fallback"})
    return fallback, generation_log
