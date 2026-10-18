"""
Shared test fixtures for the 15-question sets: a realistic list of 15 different
buyer questions, and a generator of obviously different filler questions (none
repeats or rewords another, so they pass the near-duplicate check).
"""

# 15 different buyer questions for a browser-side script security product.
QUESTIONS_15 = [
    "How can I see which scripts run on my checkout page?",
    "What tools detect card skimming code on a website?",
    "How do I keep third party tags compliant with privacy laws?",
    "How can a small team audit the JavaScript on its site?",
    "What should I check before adding a new tracking script?",
    "Who is responsible for payment page security under PCI DSS 4.0?",
    "How do attackers inject malicious code into online stores?",
    "Can I block unauthorized cookies before visitors consent?",
    "What does a consent banner need to record for regulators?",
    "Which monitoring catches a compromised analytics vendor quickly?",
    "How often should I rescan my site for new trackers?",
    "What is the difference between a CSP header and script monitoring?",
    "How do I prove to an auditor that my pages are protected?",
    "Why did my site start sending data to unknown domains?",
    "How much does client side protection usually cost a mid sized shop?",
]


def distinct_questions(start: int, count: int) -> list[str]:
    """`count` questions that share only a few words, each with its own two unique tokens."""
    return [f"How do teams handle zone{2 * k} and zone{2 * k + 1} issues?" for k in range(start, start + count)]
