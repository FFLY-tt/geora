"""
Shared plain-language formatting rules for DeepSeek-generated text that
ends up folded into a DimensionScore's `plain_finding`, so every engine
enforces the same bar instead of each hand-rolling its own check.

The bar: no em dashes, no parentheses or brackets, no leaked vendor or
model names, no citation markers, short direct sentences a
non-technical person can follow out loud. DeepSeek is told this in its
system prompt (see engines._report_style_guide), and the output is
checked afterward, because a system prompt instruction is a strong
nudge, not a guarantee.
"""

import re

# Included verbatim in every DeepSeek system prompt that produces text
# destined for plain_finding.
PLAIN_LANGUAGE_FORMAT_RULES = """
Formatting rules for every piece of text you write in this response:
- Never use an em dash or a double hyphen.
- Never use parentheses or square brackets.
- Write short, direct sentences. Say it the way you would explain it out
  loud to someone with no technical background. Do not nest one clause
  inside another.
""".strip()

_PAREN_OR_BRACKET_PATTERN = re.compile(r"[()\[\]]")
_CITATION_MARKER_PATTERN = re.compile(r"\[\s*\d+\s*\]")

# The three plain_finding field labels are added by the frontend, never by
# the model — a value that starts with its own label is a duplicate ready
# to render as "What we found: What we found: ...".
_LEAKED_LABEL_PATTERN = re.compile(
    r"^\s*(what\s+we\s+found|what\s+it'?s?\s+costing\s+you|fix\s+it\s+today)\s*:?\s*",
    re.IGNORECASE,
)

# Vendor/model names that must never leak into founder-facing text — the
# report is about the customer's brand, not about which AI tools we used
# to check it. Matched case-insensitively on word boundaries.
_BANNED_VENDOR_NAMES = ["deepseek", "perplexity", "chatgpt", "claude", "gpt", "llm", "rag"]
_VENDOR_NAME_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in _BANNED_VENDOR_NAMES) + r")\b",
    re.IGNORECASE,
)

# Site-wide terminology: "customer", never "buyer" — see AccordionItem.jsx
# and Hero.jsx on the frontend, which were unified the same way.
_BUYER_WORD_PATTERN = re.compile(r"\bbuyers?\b", re.IGNORECASE)


def find_banned_formatting(text: str) -> list[str]:
    """
    Return the distinct kinds of banned formatting found in `text`
    (em dash, double hyphen, parentheses, square brackets), or an empty
    list if `text` is clean.
    """
    found = []
    if "—" in text:
        found.append("em dash")
    if "--" in text:
        found.append("double hyphen")
    if _PAREN_OR_BRACKET_PATTERN.search(text):
        found.append("parentheses or brackets")
    return found


def find_report_violations(text: str) -> list[str]:
    """
    Full founder-report cleanliness check: banned formatting (see
    find_banned_formatting) plus leaked vendor/model names, citation
    markers like "[1]", and a leaked field label (see
    _LEAKED_LABEL_PATTERN). Used to validate AI-authored plain_finding
    field values specifically, on top of the formatting-only check other
    engines use.
    """
    found = find_banned_formatting(text)
    vendor_hits = {match.group(1).lower() for match in _VENDOR_NAME_PATTERN.finditer(text)}
    if vendor_hits:
        found.append(f"vendor or model name: {', '.join(sorted(vendor_hits))}")
    if _CITATION_MARKER_PATTERN.search(text):
        found.append("citation marker")
    if _LEAKED_LABEL_PATTERN.match(text):
        found.append("leaked field label")
    if _BUYER_WORD_PATTERN.search(text):
        found.append("used 'buyer' instead of 'customer'")
    return found


def strip_leaked_label(text: str) -> str:
    """Remove a leaked field label from the start of `text`, if present."""
    return _LEAKED_LABEL_PATTERN.sub("", text).strip()


def sanitize_banned_formatting(text: str) -> str:
    """
    Deterministic last-resort cleanup, used only if DeepSeek still
    returns banned formatting after being asked to regenerate. Never the
    first choice: a real regeneration attempt always happens first, this
    just guarantees the text we finally show a user never contains the
    banned characters even if the model doesn't cooperate.
    """
    cleaned = text.replace("--", ", ").replace("—", ", ")
    cleaned = _PAREN_OR_BRACKET_PATTERN.sub("", cleaned)
    cleaned = _CITATION_MARKER_PATTERN.sub("", cleaned)
    cleaned = _VENDOR_NAME_PATTERN.sub("AI search tools", cleaned)
    cleaned = re.sub(r"\bbuyers\b", "customers", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bbuyer\b", "customer", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r",\s*,", ",", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()
