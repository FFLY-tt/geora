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
        # v3.1: the blockquote has no attribution, so only the named Review counts.
        self.assertEqual([i["key"] for i in items], ["a customer"])

    def test_blockquote_that_already_has_a_written_attribution_is_not_counted_twice(self):
        html = "<html><body><blockquote>“Great.” — Dana Derricks, Founder @ FollowUp.com</blockquote></body></html>"
        # v3.1: both rules match, under the same person key, and merge into one item.
        _text, items = evidence_engine.extract_page_evidence("https://acme.com", "home", html)
        quotes = [i for i in evidence_engine.merge_evidence([items]) if i["category"] == "attributed_quote"]
        self.assertEqual([i["key"] for i in quotes], ["dana derricks"])


class NamedCustomerRuleTests(unittest.TestCase):
    def test_trusted_by_text_list(self):
        items = evidence_engine.find_named_customers_in_text("Trusted by Instacart, Reddit, and Gusto to monitor scripts.")
        self.assertEqual([i["text"] for i in items], ["Instacart", "Reddit", "Gusto"])

    def test_teams_at_wording_is_not_a_customer_phrase(self):
        # v3.1 only accepts "trusted by" / "customers" wording.
        items = evidence_engine.find_named_customers_in_text("Security teams at Instacart, Reddit, and Gusto use it.")
        self.assertEqual(items, [])

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
        numbers = [i for i in result.details["items"] if i["category"] == "number_or_statistic" and i["key"] == "count:11500"]
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
        self.assertEqual(evidence_engine.METHOD_VERSION, "v3.1-rules")

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


# ---------------------------------------------------------------------------
# v3.1 rule changes, one class per rule, positive and negative cases
# ---------------------------------------------------------------------------

class V31PageSelectionTests(unittest.TestCase):
    def _pages(self, html, home="https://acme.com"):
        return evidence_engine._select_pages(home, BeautifulSoup(html, "html.parser"))

    def test_index_html_is_the_homepage(self):
        self.assertEqual(evidence_engine._normalize_url("https://www.acme.com/index.html"), "https://acme.com")
        self.assertEqual(evidence_engine._normalize_url("https://acme.com/docs/index.htm"), "https://acme.com/docs")
        self.assertEqual(evidence_engine._normalize_url("https://acme.com/index-of-things"), "https://acme.com/index-of-things")

    def test_link_back_to_index_html_is_not_a_key_page(self):
        pages = self._pages('<a href="/index.html">About home</a><a href="/about">About</a>', home="https://acme.com/index.html")
        self.assertEqual(pages, [("about", "https://acme.com/about")])

    def test_exact_first_segment_beats_a_shorter_path_containing_the_word(self):
        html = '<a href="/us-privacy-compliance">x</a><a href="/security">y</a>'
        self.assertEqual(self._pages(html), [("security", "https://acme.com/security")])

    def test_trust_subdomain_counts_as_exact(self):
        html = '<a href="/blog/security-tips">x</a><a href="https://trust.acme.com/">y</a>'
        self.assertEqual(self._pages(html), [("security", "https://trust.acme.com")])

    def test_without_an_exact_match_the_shortest_containing_path_wins(self):
        html = '<a href="/state-privacy-laws-compliance-ccpa">x</a><a href="/hipaa-compliance">y</a>'
        self.assertEqual(self._pages(html), [("security", "https://acme.com/hipaa-compliance")])

    def test_link_text_alone_no_longer_selects_a_page(self):
        self.assertEqual(self._pages('<a href="/p/42">Customers</a>'), [])


class V31NumberTests(unittest.TestCase):
    def _keys(self, text, kind="home"):
        return [i["key"] for i in evidence_engine.find_numbers(text, kind)]

    def test_value_plus_unit_dedup(self):
        text = "11,500+ teams. Over 11,500 + users. 11,500+ customers. 4.7 rating. Rated 4.7 on G2. 4.7 / 5."
        items = evidence_engine.merge_evidence([[{**i, "source_url": "u"} for i in evidence_engine.find_numbers(text)]])
        self.assertEqual(sorted(i["key"] for i in items), ["count:11500", "rating:4.7"])

    def test_different_values_or_units_stay_separate(self):
        keys = self._keys("11,500+ teams. 11,600+ teams. 40% faster. 40x faster. $40M raised.")
        self.assertEqual(len(set(keys)), 5)

    def test_magnitudes_dedup_by_value(self):
        keys = set(self._keys("500K small businesses. 500K+ users. 2B+ calls. 2 billion calls."))
        self.assertEqual(keys, {"count:500000", "count:2e+09"})

    def test_pricing_page_numbers_never_count(self):
        text = "Up to 100 users. 2,000+ integrations. 99.9% uptime SLA."
        self.assertEqual(self._keys(text, kind="pricing"), [])
        self.assertTrue(self._keys(text, kind="home"))

    def test_excluded_sentence_words(self):
        for sentence in [
            "401k with 6% match.",
            "Close pays 95% of all monthly premiums for employees.",
            "Penalties up to $7,500 per violation.",
            "Get 80% off Plus for 3 months.",
            "Credit: 2.9% fee per transaction.",
            "Save 30% with an annual discount.",
            "Start a 14-day trial with 5+ seats.",
            "Unlimited PTO and 20+ vacation days.",
            "Health insurance covers 100% of costs.",
            "Fines of up to 4% of revenue.",
            "Competitive salary and 10% bonus.",
            "Great benefits, 401(k) and 3x more.",
        ]:
            self.assertEqual(self._keys(sentence), [], sentence)

    def test_exclusion_only_applies_to_its_own_sentence(self):
        self.assertEqual(self._keys("Get 80% off today. Customers grew revenue by 35%."), ["percent:35"])

    def test_exclusion_words_inside_other_words_do_not_exclude(self):
        self.assertEqual(self._keys("Offices in 12 countries. Finest tools and 99.9% uptime."), ["count:12", "percent:99.9"])


class V31AttributionTests(unittest.TestCase):
    def _items(self, html):
        return evidence_engine.find_html_testimonials(BeautifulSoup(html, "html.parser"))

    def test_blockquote_without_attribution_does_not_count(self):
        self.assertEqual(self._items("<blockquote>Our speed is intense and this helps us move fast.</blockquote>"), [])

    def test_blockquote_with_cite_name_counts(self):
        items = self._items("<blockquote>Our speed is intense and this helps us move fast.<cite>Karri Saarinen</cite></blockquote>")
        self.assertEqual([i["key"] for i in items], ["karri saarinen"])

    def test_figcaption_with_title_and_company_counts(self):
        html = "<figure><blockquote>It changed how we sell.</blockquote><figcaption>Head of Sales, Acme Robotics</figcaption></figure>"
        self.assertEqual(len(self._items(html)), 1)

    def test_cite_that_is_neither_a_name_nor_title_and_company_does_not_count(self):
        self.assertEqual(self._items("<blockquote>It changed how we sell.<cite>verified review</cite></blockquote>"), [])

    def test_schema_review_without_author_does_not_count(self):
        html = '<script type="application/ld+json">{"@type": "Review", "reviewBody": "Great product, saved us hours."}</script>'
        self.assertEqual(self._items(html), [])

    def test_blockquote_and_written_attribution_of_one_testimonial_count_once(self):
        html = (
            "<html><body><blockquote>“We doubled our pipeline.”<cite>Dana Derricks</cite></blockquote>"
            "<p>“We doubled our pipeline.” — Dana Derricks, Founder @ FollowUp.com</p></body></html>"
        )
        _text, items = evidence_engine.extract_page_evidence("https://acme.com", "home", html)
        quotes = [i for i in evidence_engine.merge_evidence([items]) if i["category"] == "attributed_quote"]
        self.assertEqual(len(quotes), 1)


class V31NamedCustomerTests(unittest.TestCase):
    LOGOS = (
        "<html><head><title>Acme | The CRM for teams</title></head><body><div class=\"customer-logos\">"
        "<img alt=\"Instacart\"><img alt=\"Acme\"><img alt=\"Google Slack Zoom\"><img alt=\"Bain & Company\"></div></body></html>"
    )

    def _customers(self, kind, html=None):
        html = html or self.LOGOS
        brands = evidence_engine._brands_from_page("acme.com", BeautifulSoup(html, "html.parser"))
        _text, items = evidence_engine.extract_page_evidence("https://acme.com/x", kind, html, brands)
        return [i["text"] for i in items if i["category"] == "named_customer_or_case_study"]

    def test_found_on_home_and_customers_pages(self):
        self.assertEqual(self._customers("home"), ["Instacart"])
        self.assertEqual(self._customers("customers"), ["Instacart"])

    def test_not_found_on_other_pages(self):
        for kind in ("security", "pricing", "about"):
            self.assertEqual(self._customers(kind), [], kind)

    def test_own_brand_from_title_is_excluded(self):
        html = (
            "<html><head><title>Northwind: Inventory software</title></head><body><div class=\"customer-logos\">"
            "<img alt=\"Northwind\"><img alt=\"Gusto\"></div></body></html>"
        )
        self.assertEqual(self._customers("home", html), ["Gusto"])

    def test_alt_with_several_names_is_dropped_not_split(self):
        found = self._customers("home")
        for name in ("Google Slack Zoom", "Google", "Slack", "Zoom"):
            self.assertNotIn(name, found)

    def test_logo_outside_any_customer_area_does_not_count(self):
        self.assertEqual(self._customers("home", '<html><body><div class="hero"><img alt="Gusto"></div></body></html>'), [])

    def test_customers_heading_marks_a_logo_area(self):
        html = '<html><body><section><h3>Customers</h3><div><img alt="Gusto"></div></section></body></html>'
        self.assertEqual(self._customers("home", html), ["Gusto"])


class V31ThirdPartyTests(unittest.TestCase):
    def _labels(self, text, kind="home"):
        return [i["text"] for i in evidence_engine.find_third_party(text, kind)]

    def test_review_sites_count_on_any_page(self):
        for kind in ("home", "pricing", "about", "customers", "security"):
            self.assertEqual(self._labels("Loved on G2 and Capterra.", kind), ["G2", "Capterra"], kind)

    def test_standards_in_a_product_feature_list_do_not_count(self):
        self.assertEqual(self._labels("Covers PCI DSS, HIPAA, GDPR, SOC 2 and ISO 27001 requirements for your website."), [])

    def test_standards_count_with_a_certification_word_in_the_same_sentence(self):
        self.assertEqual(self._labels("Acme is SOC 2 Type II certified."), ["SOC 2"])
        self.assertEqual(self._labels("We are GDPR compliant."), ["GDPR"])
        self.assertEqual(self._labels("Request our ISO 27001 report."), ["ISO 27001"])

    def test_certification_word_in_another_sentence_does_not_help(self):
        self.assertEqual(self._labels("We help with HIPAA. Our team is certified in first aid."), [])

    def test_standards_count_anywhere_on_the_security_page(self):
        self.assertEqual(self._labels("HIPAA and GDPR.", kind="security"), ["HIPAA", "GDPR"])


class V31FetchFailureTests(unittest.TestCase):
    def _run(self, exc):
        with patch.object(evidence_engine, "_fetch_html", side_effect=exc):
            return evidence_engine.check_evidence_density("acme.com")

    def _http_error(self, code):
        response = requests.Response()
        response.status_code = code
        return requests.HTTPError(str(code), response=response)

    def test_403_is_recorded_as_blocked(self):
        result = self._run(self._http_error(403))
        self.assertEqual(result.score, -1)
        self.assertEqual(result.details["fetch_failure"], "blocked: HTTP 403")
        self.assertIn("couldn't read this website", result.details["plain_finding"]["what_we_found"])

    def test_404_is_an_http_error_not_blocked(self):
        self.assertEqual(self._run(self._http_error(404)).details["fetch_failure"], "http error: HTTP 404")

    def test_timeout_is_unreachable(self):
        self.assertEqual(self._run(requests.ConnectTimeout("t")).details["fetch_failure"], "unreachable: ConnectTimeout")

    def test_a_readable_site_has_no_fetch_failure(self):
        with patch.object(evidence_engine, "_fetch_html", return_value=("https://acme.com", "<p>Hello there.</p>")):
            result = evidence_engine.check_evidence_density("acme.com")
        self.assertNotIn("fetch_failure", result.details)
        self.assertEqual(result.score, 0)


if __name__ == "__main__":
    unittest.main()
