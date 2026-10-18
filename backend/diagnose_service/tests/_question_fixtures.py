"""
Shared test fixtures for the 15-question sets: a realistic list of 15 different
buying questions ("which product should I choose"), and a generator of obviously
different filler buying questions (none repeats or rewords another, so they pass
the near-duplicate check).
"""

# 15 different buying questions for a browser-side script security product.
QUESTIONS_15 = [
    "What is the best tool to monitor third party scripts on a checkout page?",
    "Alternatives to a basic content security policy for a small online store?",
    "Which software detects card skimming code on an ecommerce site?",
    "Best PCI DSS 4.0 compliance tools for a mid sized retailer?",
    "Client side security platforms for a team with no security staff?",
    "What is the best consent management platform for a European shop?",
    "Cookie scanning tools for a site with many marketing tags?",
    "Compare website tag governance products for a large enterprise",
    "Which monitoring software alerts me when a vendor script changes?",
    "Best privacy compliance software for a healthcare provider website?",
    "Top rated tools for auditing JavaScript on a banking site?",
    "What software should a SaaS startup choose to prove compliance to auditors?",
    "Tracker blocking tools that do not slow down the website?",
    "Affordable script monitoring services for a small business?",
    "Which platform is best for an agency managing many client websites?",
]


def distinct_questions(start: int, count: int) -> list[str]:
    """`count` buying questions that share only a few words, each with its own two unique tokens."""
    return [f"Best tool for zone{2 * k} and zone{2 * k + 1} teams?" for k in range(start, start + count)]
