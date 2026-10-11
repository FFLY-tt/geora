"""
Tests for the rule-based evidence_engine.

Fully offline: pages are mocked HTML strings, nothing touches the
network, and there is no LLM or cache involved to mock.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from bs4 import BeautifulSoup

_THIS_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _THIS_DIR.parent  # backend/diagnose_service
_BACKEND_DIR = _SERVICE_DIR.parent  # backend/

sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))

import engines.evidence_engine as evidence_engine  # noqa: E402

# Close-style homepage text: a data-card strip with no punctuation between
# the stats, then testimonials attributed "Name, Title @ Company".
CLOSE_STYLE = (
    "11,500 + Sales teams trust Close 2 B+ Calls, emails, and texts 4.7 · Rated on G2 "
    "“Our booked appointments per week have gone up ~50% since Chloe went live.” "
    "— Dana Derricks, Founder @ FollowUp.com "
    "“We've saved over $100,000 in tech costs switching from Salesforce to Close.” "
    "— Zac Muir, VP of Revenue @ Sisu "
    "“Close changed how we sell.” — Jem Millett, Director of Admissions at Flatiron School"
)

# Linear-style text from a mocked-up app screenshot embedded in the page.
LINEAR_UI = (
    "Linear created the issue via Slack on behalf of Karri · 2min ago "
    "Linear connected by Jori · 2 min ago Worked for 8 sec Worked for 1 min "
    "didier Has anyone been looking into the iOS startup performance issues? "
    "kinetic-ios/src/screens/Home/HomeScreen.tsx"
)


def _types(items):
    return {(i["category"], i["text"]) for i in items}


class NumberRuleTests(unittest.TestCase):
    def test_data_cards_without_punctuation_are_independent_evidence(self):
        texts = [i["text"] for i in evidence_engine.find_numbers(CLOSE_STYLE)]
        self.assertIn("11,500 +", texts)
        self.assertIn("2 B+", texts)
        self.assertTrue(any(t.startswith("4.7") for t in texts))
        self.assertIn("50%", texts)
        self.assertIn("$100,000", texts)
        self.assertEqual(len({i["key"] for i in evidence_engine.find_numbers(CLOSE_STYLE)}), 5)

    def test_units_and_symbols(self):
        text = "We deliver 10x faster results with 99.9% uptime for 40% less, 2000+ customers, 1.5 million users, 5M calls."
        texts = [i["text"] for i in evidence_engine.find_numbers(text)]
        for expected in ["10x", "99.9%", "40%", "2000+ customers", "1.5 million", "5M"]:
            self.assertIn(expected, texts)

    def test_ui_screenshot_text_is_not_evidence(self):
        self.assertEqual(evidence_engine.find_numbers(LINEAR_UI), [])
        self.assertEqual(evidence_engine.find_attributions(LINEAR_UI), [])
        self.assertEqual(evidence_engine.find_named_customers_in_text(LINEAR_UI), [])
        self.assertEqual(evidence_engine.find_third_party(LINEAR_UI), [])

    def test_number_next_to_ago_is_ui_noise(self):
        self.assertEqual(evidence_engine.find_numbers("Created 12+ issues 2min ago"), [])

    def test_years_phone_numbers_dates_and_versions_are_not_numbers(self):
        text = (
            "Founded in 2012. Call 1-800-555-0199 or (415) 555-1234 or +1 415 555 1234. "
            "Updated October 20, 2026 and 03/04/2025. Version 4.0.1 covers req 6.4.3 and 11.6.1."
        )
        self.assertEqual(evidence_engine.find_numbers(text), [])

    def test_plan_prices_are_not_numbers_but_customer_results_are(self):
        text = "Pro is $49 per user/month. Team $15/user/mo. Billed $120 annually. Customers saved $100,000 last year."
        texts = [i["text"] for i in evidence_engine.find_numbers(text)]
        self.assertEqual(texts, ["$100,000"])

    def test_no_currency_at_all_is_counted_on_a_pricing_page(self):
        text = "Starter $29 Business $99 Enterprise $499"
        self.assertEqual(evidence_engine.find_numbers(text, page_kind="pricing"), [])
        self.assertTrue(evidence_engine.find_numbers(text, page_kind="home"))

    def test_rating_needs_a_rating_word(self):
        self.assertTrue(evidence_engine.find_numbers("Rated 4.7 on G2"))
        self.assertTrue(evidence_engine.find_numbers("4.7 out of 5"))
        self.assertEqual(evidence_engine.find_numbers("Step 4.7 of the guide"), [])


class AttributionRuleTests(unittest.TestCase):
    def test_close_style_name_title_company(self):
        items = evidence_engine.find_attributions(CLOSE_STYLE)
        texts = [i["text"] for i in items]
        self.assertIn("Dana Derricks, Founder @ FollowUp.com", texts)
        self.assertTrue(any(t.startswith("Zac Muir, VP of Revenue @ Sisu") for t in texts))
        self.assertIn("Jem Millett, Director of Admissions @ Flatiron School", texts)
        self.assertEqual(len(items), 3)

    def test_dash_name_company_after_a_quote(self):
        items = evidence_engine.find_attributions("“It just works.” — Priya Nair, Acme Robotics")
        self.assertEqual([i["text"] for i in items], ["Priya Nair, Acme Robotics"])

    def test_dash_without_a_quote_is_not_an_attribution(self):
        self.assertEqual(evidence_engine.find_attributions("Contact Sales — Pricing Plans, Acme Robotics"), [])

    def test_a_title_without_a_role_word_is_not_an_attribution(self):
        self.assertEqual(evidence_engine.find_attributions("Dana Derricks, loves pizza @ FollowUp.com"), [])

    def test_blockquote_and_schema_review_blocks(self):
        html = (
            "<blockquote>The onboarding was painless and support answered within minutes every time.</blockquote>"
            '<script type="application/ld+json">{"@type": "Review", "reviewBody": "Great product, saved us hours every week.", '
            '"author": {"name": "A. Customer"}}</script>'
        )
        items = evidence_engine.find_html_testimonials(BeautifulSoup(html, "html.parser"))
        self.assertEqual(len(items), 2)

    def test_blockquote_that_already_has_a_written_attribution_is_not_counted_twice(self):
        html = "<blockquote>“Great.” — Dana Derricks, Founder @ FollowUp.com</blockquote>"
        self.assertEqual(evidence_engine.find_html_testimonials(BeautifulSoup(html, "html.parser")), [])


class NamedCustomerRuleTests(unittest.TestCase):
    def test_trusted_by_text_list(self):
        items = evidence_engine.find_named_customers_in_text(
            "Security teams at Instacart, Reddit, and Gusto use it to monitor scripts."
        )
        self.assertEqual([i["text"] for i in items], ["Instacart", "Reddit", "Gusto"])

    def test_taglines_and_numbers_are_not_customers(self):
        for text in ["Powering the companies building the future", "Trusted by 11,500+ sales teams", "Used by the best"]:
            self.assertEqual(evidence_engine.find_named_customers_in_text(text), [], text)

    def test_logo_area_alt_texts(self):
        html = (
            '<header><img alt="Acme logo" src="a.png"></header>'
            '<div class="trusted-by"><img alt="Instacart logo"><img alt="Reddit"><img alt="logo"></div>'
            '<div class="hero"><img alt="Unrelated screenshot"></div>'
        )
        items = evidence_engine.find_named_customers_in_html(BeautifulSoup(html, "html.parser"), own_label="acme")
        self.assertEqual([i["text"] for i in items], ["Instacart", "Reddit"])

    def test_logos_after_a_trusted_by_heading(self):
        html = '<section><h4>Trusted by teams at</h4><div><img alt="Gusto"><img alt="Notion"></div></section>'
        items = evidence_engine.find_named_customers_in_html(BeautifulSoup(html, "html.parser"))
        self.assertEqual([i["text"] for i in items], ["Gusto", "Notion"])

    def test_schema_org_customers(self):
        html = '<script type="application/ld+json">{"@type": "Organization", "customer": [{"name": "Instacart"}, {"name": "Gusto"}]}</script>'
        items = evidence_engine.find_named_customers_in_html(BeautifulSoup(html, "html.parser"))
        self.assertEqual([i["text"] for i in items], ["Instacart", "Gusto"])


class ThirdPartyRuleTests(unittest.TestCase):
    def test_vocabulary_matches_once_per_term(self):
        text = "Rated on G2 and Capterra. G2 leader. SOC 2 Type II, ISO 27001, HIPAA, GDPR, PCI DSS, Gartner, Forrester, Trustpilot."
        labels = [i["text"] for i in evidence_engine.find_third_party(text)]
        for expected in ["G2", "Capterra", "Gartner", "Forrester", "SOC 2", "ISO 27001", "HIPAA", "GDPR", "PCI DSS", "Trustpilot"]:
            self.assertIn(expected, labels)
        self.assertEqual(labels.count("G2"), 1)

    def test_g2_inside_another_token_does_not_match(self):
        self.assertEqual(evidence_engine.find_third_party("model XG2000 and ABG2"), [])


# ---------------------------------------------------------------------------
# Page selection and the full engine
# ---------------------------------------------------------------------------

HOME_HTML = """
<html><body>
<nav><a href="/customers">Customers</a> <a href="/security">Security</a> <a href="/pricing">Pricing</a>
<a href="/about">About</a> <a href="https://other.com/customers">Elsewhere</a> <a href="/docs.pdf">PDF</a></nav>
<p>11,500 + Sales teams trust Acme. 4.7 · Rated on G2.</p>
<a href="/customers/">Customers again</a> <a href="/customers#top">Customers anchor</a>
<a href="/blog/about-us-post">Our story</a>
</body></html>
"""
CUSTOMERS_HTML = "<html><body><p>11,500 + Sales teams trust Acme. “Great.” — Dana Derricks, Founder @ FollowUp.com</p></body></html>"
SECURITY_HTML = "<html><body><p>Acme is SOC 2 and ISO 27001 certified. Rated on G2.</p></body></html>"
PRICING_HTML = "<html><body><p>Pro $49 per user/month. Team $99 billed annually.</p></body></html>"
ABOUT_HTML = "<html><body><p>Founded in 2012. Call 1-800-555-0199.</p></body></html>"


def _fake_fetch(pages):
    calls = []

    def fetch(url):
        calls.append(url)
        key = (url if url.startswith("http") else f"https://{url}").rstrip("/")
        if key not in pages:
            raise requests.ConnectionError(f"no such page {url}")
        value = pages[key]
        if isinstance(value, Exception):
            raise value
        return key, value

    fetch.calls = calls
    return fetch


PAGES = {
    "https://acme.com": HOME_HTML,
    "https://acme.com/customers": CUSTOMERS_HTML,
    "https://acme.com/security": SECURITY_HTML,
    "https://acme.com/pricing": PRICING_HTML,
    "https://acme.com/about": ABOUT_HTML,
}


class PageSelectionTests(unittest.TestCase):
    def test_one_page_per_kind_normalized_deduplicated_same_site_only(self):
        pages = evidence_engine._select_pages("https://acme.com", BeautifulSoup(HOME_HTML, "html.parser"))
        self.assertEqual(
            pages,
            [
                ("customers", "https://acme.com/customers"),
                ("security", "https://acme.com/security"),
                ("pricing", "https://acme.com/pricing"),
                ("about", "https://acme.com/about"),
            ],
        )

    def test_missing_kinds_are_simply_not_read(self):
        html = '<a href="/pricing">Pricing</a><a href="/blog">Blog</a>'
        pages = evidence_engine._select_pages("https://acme.com", BeautifulSoup(html, "html.parser"))
        self.assertEqual(pages, [("pricing", "https://acme.com/pricing")])

    def test_link_text_can_identify_a_page_and_path_wins_over_text(self):
        html = '<a href="/p/42">Case studies customers</a><a href="/customers">Nope</a>'
        pages = evidence_engine._select_pages("https://acme.com", BeautifulSoup(html, "html.parser"))
        self.assertEqual(pages, [("customers", "https://acme.com/customers")])

    def test_subdomain_trust_center_is_same_site(self):
        html = '<a href="https://trust.acme.com/">Trust center</a>'
        pages = evidence_engine._select_pages("https://www.acme.com/", BeautifulSoup(html, "html.parser"))
        self.assertEqual(pages, [("security", "https://trust.acme.com")])


class EngineTests(unittest.TestCase):
    def _run(self, pages=PAGES):
        fetch = _fake_fetch(pages)
        with patch.object(evidence_engine, "_fetch_html", side_effect=fetch):
            return evidence_engine.check_evidence_density("acme.com"), fetch

    def test_reads_homepage_plus_found_key_pages_and_records_sources(self):
        result, fetch = self._run()
        kinds = [p["kind"] for p in result.details["pages_read"]]
        self.assertEqual(kinds, ["home", "customers", "security", "pricing", "about"])
        for item in result.details["items"]:
            self.assertTrue(item["source_url"].startswith("https://acme.com"))
            self.assertIn(item["source_page"], kinds)
            self.assertTrue(item["text"] and item["context"])

    def test_same_evidence_on_two_pages_is_counted_once(self):
        result, _ = self._run()
        numbers = [i for i in result.details["items"] if i["category"] == "number_or_statistic" and i["key"] == "11500+"]
        self.assertEqual(len(numbers), 1)
        self.assertEqual(numbers[0]["source_page"], "home")
        self.assertEqual(numbers[0]["also_found_on"], ["https://acme.com/customers"])
        g2 = [i for i in result.details["items"] if i["category"] == "third_party_source_or_certification" and i["key"] == "g2"]
        self.assertEqual(len(g2), 1)

    def test_each_category_found_and_pricing_and_about_noise_excluded(self):
        result, _ = self._run()
        counts = result.details["counts_by_category"]
        self.assertEqual(counts["attributed_quote"], 1)
        self.assertEqual(counts["third_party_source_or_certification"], 3)  # G2, SOC 2, ISO 27001
        number_texts = [i["text"] for i in result.details["items"] if i["category"] == "number_or_statistic"]
        self.assertFalse(any("$" in t for t in number_texts))
        self.assertFalse(any("2012" in t or "800" in t for t in number_texts))
        self.assertEqual(result.details["pages_by_category"]["attributed_quote"], {"customers": 1})

    def test_score_is_the_documented_formula(self):
        result, _ = self._run()
        counts = result.details["counts_by_category"]
        expected = sum(evidence_engine._points_for_item_count(c) for c in counts.values())
        self.assertEqual(result.score, expected)
        self.assertIn("Our own fixed formula", result.details["scoring_rule"])
        self.assertEqual(evidence_engine.METHOD_VERSION, "v3-rules")

    def test_same_input_twice_gives_identical_results(self):
        first, _ = self._run()
        second, _ = self._run()
        self.assertEqual(first.score, second.score)
        self.assertEqual(first.details, second.details)

    def test_no_evidence_is_a_zero_score_not_insufficient_data(self):
        result, _ = self._run({"https://acme.com": "<html><body><p>We build great software for everyone.</p></body></html>"})
        self.assertEqual(result.score, 0)
        self.assertEqual(result.details["status"], "ok")
        self.assertEqual(result.details["items"], [])

    def test_a_failed_key_page_is_recorded_and_the_rest_still_scored(self):
        pages = {**PAGES, "https://acme.com/security": requests.ConnectionError("boom")}
        result, _ = self._run(pages)
        self.assertEqual(result.details["status"], "ok")
        self.assertEqual([f["kind"] for f in result.details["pages_failed"]], ["security"])
        self.assertNotIn("security", [p["kind"] for p in result.details["pages_read"]])

    def test_homepage_fetch_failure_is_insufficient_data(self):
        result, _ = self._run({})
        self.assertEqual(result.score, -1)
        self.assertEqual(result.details["status"], "insufficient_data")

    def test_engine_makes_no_llm_or_cache_calls(self):
        for name in ("make_plain_finding", "get_cached_judgment", "save_judgment", "_call_deepseek_for_evidence_listing"):
            self.assertFalse(hasattr(evidence_engine, name), name)


if __name__ == "__main__":
    unittest.main()
