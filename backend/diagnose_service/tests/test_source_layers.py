"""
Tests for the layered diagnosis: domain classification, page types for other
companies' pages, the "does this page mention you" rules, and reliance,
presence, gap, coverage and the conclusion built from them.

Fully offline: page fetches are replaced by a fake that returns HTML, nothing
touches the network and no LLM is involved.
"""

import sys
import unittest
from fractions import Fraction
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _THIS_DIR.parent  # backend/diagnose_service
_BACKEND_DIR = _SERVICE_DIR.parent  # backend/

sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))

import source_layers  # noqa: E402
from engines._source_layers import (  # noqa: E402
    LAYER_LIST_VERSION,
    LAYER_ORDER,
    PAGE_TYPE_RULES_VERSION,
    classify_host,
    classify_page_type,
    classify_url,
)

OWN = "acme.com"


class ClassificationTests(unittest.TestCase):
    def layer(self, url):
        return classify_url(url, OWN)

    def test_reference_sites(self):
        for url in [
            "https://en.wikipedia.org/wiki/Skimming",
            "https://www.nist.gov/cyberframework",
            "https://owasp.org/www-project-top-ten/",
            "https://www.iso.org/standard/27001",
            "https://www.pcisecuritystandards.org/document_library",
            "https://cs.stanford.edu/people",
            "https://www.gov.uk/guidance/gdpr",
            "https://www.unsw.edu.au/research",
            "https://coveryourtracks.eff.org/",
            "https://www.eff.org/issues",
        ]:
            self.assertEqual(self.layer(url), "reference", url)

    def test_review_and_media_sites(self):
        for url in [
            "https://www.g2.com/products/acme/reviews",
            "https://www.capterra.com/p/1/Acme/",
            "https://www.gartner.com/reviews/market/x",
            "https://www.trustradius.com/products/acme",
            "https://www.getapp.com/x",
            "https://www.softwareadvice.com/x",
            "https://techcrunch.com/2026/01/01/story",
            "https://www.pcmag.com/reviews/acme",
            "https://uk.pcmag.com/smb-accounting/1/best",
            "https://www.nerdwallet.com/business/software/best/invoicing-software",
        ]:
            self.assertEqual(self.layer(url), "review_media", url)

    def test_v2_additions_to_review_media_including_news_sites(self):
        for url in [
            "https://fitsmallbusiness.com/best-crm/",
            "https://www.cnbc.com/2026/01/01/story.html",
            "https://www.businesswire.com/news/home/1",
            "https://www.cmswire.com/a",
            "https://www.techbullion.com/a",
            "https://www.smashingmagazine.com/2026/01/a/",
            "https://www.smallbiztrends.com/a",
            "https://laptopjudge.com/a",
            "https://www.sanluisobispo.com/news/local/article1.html",
            "https://www.nytimes.com/2026/01/01/a.html",
        ]:
            self.assertEqual(self.layer(url), "review_media", url)

    def test_community_sites(self):
        for url in [
            "https://www.reddit.com/r/sales/comments/1",
            "https://www.linkedin.com/pulse/x",
            "https://www.youtube.com/watch?v=1",
            "https://www.quora.com/What-is-x",
            "https://security.stackexchange.com/questions/1",
            "https://stackoverflow.com/questions/1",
            "https://medium.com/@someone/post",
            "https://someone.medium.com/post",
            "https://community.atlassian.com/t5/Jira/x/qaq-p/1",
            "https://community.hubspot.com/t5/x",
            "https://community.shopify.com/c/x",
        ]:
            self.assertEqual(self.layer(url), "community", url)

    def test_community_prefix_only_counts_as_a_subdomain_label(self):
        self.assertEqual(self.layer("https://communityfoo.com/x"), "other_companies")
        self.assertEqual(self.layer("https://example.com/community/x"), "other_companies")

    def test_own_site_includes_subdomains_and_www(self):
        for url in ["https://acme.com/", "https://www.acme.com/pricing", "https://trust.acme.com/", "https://docs.eu.acme.com/x"]:
            self.assertEqual(self.layer(url), "own_site", url)

    def test_own_site_wins_even_for_a_listed_domain_name(self):
        self.assertEqual(classify_host("blog.g2.com", "g2.com"), "own_site")

    def test_unlisted_sites_and_vendor_docs_are_other_companies(self):
        for url in [
            "https://www.competitor.io/blog/acme-vs-us",
            "https://random-blog.net/post",
            "https://example.org/",
            "https://support.microsoft.com/en-us/topic/x",
            "https://learn.microsoft.com/en-us/x",
            "https://docs.stripe.com/x",
            "https://web.dev/articles/x",
        ]:
            self.assertEqual(self.layer(url), "other_companies", url)

    def test_lookalike_domains_are_not_matched_by_substring(self):
        for url in ["https://notacme.com/", "https://acme.com.evil.io/", "https://myg2.com/", "https://wikipedia.org.fake.com/", "https://redditx.com/", "https://theeff.org.evil.io/"]:
            self.assertEqual(self.layer(url), "other_companies", url)

    def test_url_without_a_host_has_no_layer(self):
        self.assertIsNone(self.layer(""))

    def test_versions_and_order_are_fixed(self):
        self.assertEqual(LAYER_LIST_VERSION, "v2")
        self.assertEqual(PAGE_TYPE_RULES_VERSION, "v1")
        self.assertEqual(LAYER_ORDER, ["reference", "review_media", "community", "own_site", "lists", "guides", "product_pages"])


class PageTypeTests(unittest.TestCase):
    def test_lists_by_url_keyword(self):
        for url in [
            "https://x.io/best-crm-software",
            "https://x.io/blog/top-10-tools",
            "https://x.io/acme-vs-beta",
            "https://x.io/acme-versus-beta",
            "https://x.io/acme-alternatives",
            "https://x.io/crm-comparison",
            "https://x.io/compare/acme",
            "https://x.io/acme-review",
            "https://x.io/software-reviews",
        ]:
            self.assertEqual(classify_page_type(url), "lists", url)

    def test_lists_by_title_keyword(self):
        self.assertEqual(classify_page_type("https://x.io/p/42", "The 7 Best CRMs for 2026 | Example"), "lists")
        self.assertEqual(classify_page_type("https://x.io/p/42", "Acme vs. Beta: which is right?"), "lists")
        self.assertEqual(classify_page_type("https://x.io/p/42", "Top picks"), "lists")

    def test_keywords_match_whole_words_only(self):
        for url, title in [
            ("https://x.io/bestseller", None),
            ("https://x.io/stop-fraud", None),
            ("https://x.io/reviewer-tools", None),
            ("https://x.io/vsphere", None),
            ("https://x.io/p/1", "Bestway to fly; Stopwatch"),
        ]:
            self.assertEqual(classify_page_type(url, title), "product_pages", (url, title))

    def test_guides_by_path_keyword(self):
        for url in [
            "https://x.io/blog/how-we-work",
            "https://x.io/guides/setup",
            "https://x.io/guide",
            "https://x.io/how-to-track-sales",
            "https://x.io/learn/crm",
            "https://x.io/docs/api",
            "https://x.io/help/billing",
            "https://x.io/support/ticket",
            "https://x.io/knowledge/base",
            "https://x.io/academy/course",
            "https://x.io/resources/ebook",
        ]:
            self.assertEqual(classify_page_type(url), "guides", url)

    def test_vendor_docs_sites_are_guides_by_their_subdomain(self):
        for url in [
            "https://support.microsoft.com/en-us/topic/x",
            "https://learn.microsoft.com/en-us/x",
            "https://docs.stripe.com/payments",
            "https://help.sendspark.com/en/articles/1",
        ]:
            self.assertEqual(classify_page_type(url), "guides", url)

    def test_a_list_that_is_also_in_a_guide_section_is_a_list(self):
        self.assertEqual(classify_page_type("https://x.io/blog/best-crm"), "lists")
        self.assertEqual(classify_page_type("https://x.io/learn/acme-vs-beta"), "lists")

    def test_everything_else_is_a_product_page(self):
        for url, title in [
            ("https://x.io/", None),
            ("https://x.io/pricing", "Pricing | Acme"),
            ("https://x.io/features/sales-automation", None),
            ("https://x.io/product", "Our Product"),
        ]:
            self.assertEqual(classify_page_type(url, title), "product_pages", url)

    def test_the_keywords_are_not_taken_from_the_site_name(self):
        # the first label of a two-label host is the site's own name, not a subdomain
        self.assertEqual(classify_page_type("https://blog.io/pricing"), "product_pages")
        self.assertEqual(classify_page_type("https://learn.io/pricing"), "product_pages")

    def test_without_a_title_only_the_url_decides(self):
        self.assertEqual(classify_page_type("https://x.io/p/42", None), "product_pages")
        self.assertEqual(classify_page_type("https://x.io/p/42", ""), "product_pages")


def html(body="", title="Some page"):
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


class MentionRuleTests(unittest.TestCase):
    def check(self, name, domain, body, title="Some page"):
        return source_layers.analyze_page(html(body, title), name, domain)

    # ---- companies whose name is not an ordinary word
    def test_regular_name_matches_as_a_whole_word_in_the_text(self):
        hit = self.check("Feroot Security", "feroot.com", "<p>Try Feroot Security today.</p>")
        self.assertTrue(hit["mentions_you"])
        self.assertEqual(hit["rule"], "name_in_text")
        self.assertIn("Feroot Security", hit["snippet"])
        miss = self.check("Feroot Security", "feroot.com", "<p>Nothing relevant here.</p>")
        self.assertFalse(miss["mentions_you"])

    def test_regular_name_inside_another_word_does_not_match(self):
        self.assertFalse(self.check("Acme", "acme.com", "<p>The Acmeville plant.</p>")["mentions_you"])

    def test_regular_name_or_the_domain_in_the_text(self):
        hit = self.check("Feroot Security", "feroot.com", "<p>See feroot.com for details.</p>")
        self.assertEqual((hit["mentions_you"], hit["rule"]), (True, "domain_in_text"))

    # ---- common-word names, rule 1: a link to the domain
    def test_common_word_rule_link_to_the_domain(self):
        hit = self.check("Close", "close.com", '<p>Try <a href="https://www.close.com/pricing">this CRM</a>.</p>')
        self.assertEqual((hit["mentions_you"], hit["rule"]), (True, "domain_in_link"))
        self.assertIn("close.com", hit["snippet"])

    def test_common_word_rule_link_to_a_lookalike_domain_does_not_count(self):
        for href in ["https://disclose.com/a", "https://enclose.com/a", "https://close.community/a", "https://my-close.com/a", "https://close.com.au/a"]:
            hit = self.check("Close", "close.com", f'<p><a href="{href}">x</a></p>')
            self.assertFalse(hit["mentions_you"], href)

    def test_common_word_rule_a_link_to_a_subdomain_counts(self):
        self.assertTrue(self.check("Close", "close.com", '<a href="https://app.close.com/login">x</a>')["mentions_you"])

    # ---- rule 2: the domain in the text
    def test_common_word_rule_domain_in_the_text(self):
        hit = self.check("Linear", "linear.app", "<p>Compare it with linear.app for issue tracking.</p>")
        self.assertEqual((hit["mentions_you"], hit["rule"]), (True, "domain_in_text"))

    def test_common_word_rule_domain_inside_another_word_does_not_count(self):
        hit = self.check("Close", "close.com", "<p>Visit enclose.com or disclose.com.</p>")
        self.assertFalse(hit["mentions_you"])

    # ---- rule 3: the capitalized name opening a heading or list item
    def test_common_word_rule_heading_or_list_start(self):
        for body in [
            "<ol><li>1. Linear</li></ol>",
            "<h3>3. Linear</h3>",
            "<h2>Linear – best for engineering teams</h2>",
            "<ul><li>Linear: fast issue tracking</li></ul>",
            "<h4>#2 Linear</h4>",
            "<h1>Linear</h1>",
        ]:
            hit = self.check("Linear", "linear.app", body)
            self.assertEqual((hit["mentions_you"], hit["rule"]), (True, "heading_or_list_start"), body)

    def test_common_word_a_plain_sentence_does_not_count(self):
        for body in [
            "<p>Use a linear process for onboarding.</p>",
            "<p>Linear regression is a statistical method.</p>",
            "<p>We prefer Linear over Jira.</p>",
            "<div>Linear is great</div>",
        ]:
            self.assertFalse(self.check("Linear", "linear.app", body)["mentions_you"], body)

    def test_common_word_lowercase_or_inside_longer_word_does_not_open_a_heading(self):
        for body in ["<h2>linear tools</h2>", "<li>Linearity of time</li>", "<h2>Nonlinear thinking</h2>", "<li>Linear2 beta</li>"]:
            self.assertFalse(self.check("Linear", "linear.app", body)["mentions_you"], body)

    def test_common_word_only_h1_to_h4_and_list_items_count(self):
        for body in ["<h5>Linear</h5>", "<p>Linear</p>", "<table><tr><td>Linear</td></tr></table>"]:
            self.assertFalse(self.check("Linear", "linear.app", body)["mentions_you"], body)

    def test_common_word_name_in_plain_text_is_not_a_mention_but_is_kept_as_a_near_miss(self):
        miss = self.check("Close", "close.com", "<p>It is time to close the deal and move on.</p>")
        self.assertFalse(miss["mentions_you"])
        self.assertIn("close the deal", miss["near_miss"])

    def test_title_is_read_from_the_html(self):
        self.assertEqual(self.check("Acme", "acme.com", "", title="  The   Best CRMs ")["title"], "The Best CRMs")
        self.assertIsNone(source_layers.analyze_page("<p>no title</p>", "Acme", "acme.com")["title"])


class FakeFetch:
    """Stand-in for fetch_html: {url fragment: html or Exception}; any other page doesn't mention the company."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        for fragment, value in self.pages.items():
            if fragment in url:
                if isinstance(value, Exception):
                    raise value
                return value
        return html("<p>A page about something else entirely.</p>", title="Else")


def _details(*answers):
    """citation_visibility details with one successful run per answer (a list of cited URLs)."""
    runs = [{"status": "ok", "all_citations": list(urls)} for urls in answers]
    return {"per_query_results": [{"query": "q", "runs": runs}]}


def run(details, pages=None, name="Acme", domain=OWN, fetch=None):
    return source_layers.build_source_layers(name, domain, details, fetch=fetch or FakeFetch(pages or {}))


def by_layer(result):
    return {layer["layer"]: layer for layer in result["layers"]}


def mention(text="We compare Acme with others.", title="Page"):
    return html(f"<p>{text}</p>", title=title)


class RelianceAndPresenceTests(unittest.TestCase):
    def test_reliance_is_the_groups_share_of_all_citations(self):
        details = _details(
            ["https://g2.com/a", "https://c.io/best-tools"],
            ["https://g2.com/a", "https://c.io/blog/x", "https://c.io/pricing"],
        )
        layers = by_layer(run(details))
        self.assertEqual(layers["review_media"]["citations"], 2)
        self.assertEqual([layers[g]["citations"] for g in ("lists", "guides", "product_pages")], [1, 1, 1])
        self.assertAlmostEqual(layers["review_media"]["reliance"], 0.4)
        self.assertAlmostEqual(layers["lists"]["reliance"], 0.2)
        self.assertEqual(layers["reference"]["reliance"], 0)
        self.assertAlmostEqual(sum(l["reliance"] for l in layers.values()), 1.0)

    def test_a_url_cited_twice_within_one_answer_counts_once(self):
        details = _details(["https://g2.com/a", "https://www.g2.com/a/", "https://g2.com/a#top"])
        self.assertEqual(by_layer(run(details))["review_media"]["citations"], 1)

    def test_failed_answers_are_ignored(self):
        details = {"per_query_results": [{"runs": [{"status": "error"}, {"status": "ok", "all_citations": ["https://g2.com/a"]}]}]}
        self.assertEqual(run(details)["total_answers"], 1)

    def test_presence_is_weighted_by_citations(self):
        # page A cited 3 times mentions Acme, page B cited once does not: 3/4 (both are product pages)
        details = _details(["https://c.io/a", "https://o.io/b"], ["https://c.io/a"], ["https://c.io/a"])
        pages = {"c.io/a": mention(), "o.io/b": html("<p>Nothing.</p>")}
        layer = by_layer(run(details, pages))["product_pages"]
        self.assertAlmostEqual(layer["presence"], 0.75)
        self.assertEqual({p["url"]: p["mentions_you"] for p in layer["pages"]}, {"c.io/a": True, "o.io/b": False})
        self.assertIn("Acme", layer["pages"][0]["snippet"])

    def test_unknown_pages_are_left_out_of_the_denominator_and_listed(self):
        details = _details(["https://c.io/a", "https://o.io/b"], ["https://c.io/a"])
        pages = {"c.io/a": mention(), "o.io/b": ConnectionError("boom")}
        layer = by_layer(run(details, pages))["product_pages"]
        self.assertEqual(layer["presence"], 1.0)  # 2 of 2 known citations, the unknown one is not counted
        self.assertEqual([p["url"] for p in layer["unknown_pages"]], ["o.io/b"])
        self.assertEqual(layer["unknown_pages"][0]["error"], "ConnectionError")

    def test_a_group_with_only_unknown_pages_has_no_presence_or_gap(self):
        layer = by_layer(run(_details(["https://c.io/a"]), {"c.io/a": TimeoutError()}))["product_pages"]
        self.assertIsNone(layer["presence"])
        self.assertIsNone(layer["gap"])
        self.assertEqual(layer["status"], "unknown")

    def test_own_site_presence_is_just_whether_it_was_cited(self):
        cited = by_layer(run(_details(["https://acme.com/pricing"], ["https://g2.com/a"])))["own_site"]
        self.assertEqual(cited["presence"], 1.0)
        self.assertEqual(cited["gap"], 0)
        not_cited = by_layer(run(_details(["https://g2.com/a"])))["own_site"]
        self.assertEqual((not_cited["citations"], not_cited["reliance"]), (0, 0))

    def test_own_site_pages_are_never_fetched(self):
        fetch = FakeFetch({})
        run(_details(["https://acme.com/a", "https://g2.com/b"]), fetch=fetch)
        self.assertEqual(fetch.calls, ["g2.com/b"])

    def test_no_successful_answers_gives_no_layers(self):
        self.assertIsNone(run({"per_query_results": [{"runs": [{"status": "error"}]}]}))
        self.assertIsNone(run({}))


class PageTypeGroupsInTheResultTests(unittest.TestCase):
    def test_other_companies_pages_land_in_their_page_type_group_with_their_own_numbers(self):
        details = _details(
            ["https://a.io/best-crm", "https://b.io/blog/how-to-sell", "https://c.io/product"],
            ["https://a.io/best-crm", "https://b.io/blog/how-to-sell"],
        )
        pages = {"a.io/best-crm": mention(), "b.io/blog": html("<p>No.</p>"), "c.io/product": html("<p>No.</p>")}
        layers = by_layer(run(details, pages))
        self.assertEqual((layers["lists"]["citations"], layers["lists"]["presence"]), (2, 1.0))
        self.assertEqual((layers["guides"]["citations"], layers["guides"]["presence"]), (2, 0.0))
        self.assertEqual((layers["product_pages"]["citations"], layers["product_pages"]["presence"]), (1, 0.0))
        self.assertEqual({layers[g]["parent"] for g in ("lists", "guides", "product_pages")}, {"other_companies"})
        self.assertAlmostEqual(layers["guides"]["gap"], 0.4)
        self.assertAlmostEqual(layers["product_pages"]["gap"], 0.2)

    def test_the_fetched_title_decides_the_type_when_the_url_does_not(self):
        pages = {"c.io/p/42": html("<p>x</p>", title="The Best CRMs of 2026")}
        layers = by_layer(run(_details(["https://c.io/p/42"]), pages))
        self.assertEqual(layers["lists"]["citations"], 1)
        self.assertEqual(layers["lists"]["pages"][0]["title"], "The Best CRMs of 2026")

    def test_when_the_page_cannot_be_fetched_only_the_url_decides(self):
        layers = by_layer(run(_details(["https://c.io/p/42"]), {"c.io/p/42": OSError()}))
        self.assertEqual(layers["product_pages"]["citations"], 1)
        self.assertEqual(layers["lists"]["citations"], 0)

    def test_each_page_type_group_is_part_of_the_biggest_gap_decision(self):
        details = _details(["https://a.io/best-crm"], ["https://b.io/blog/x"], ["https://b.io/blog/y"], ["https://c.io/p"])
        self.assertEqual(run(details)["biggest_gap_layer"], "guides")  # 2 of 4 citations, none mention Acme


class CoverageTests(unittest.TestCase):
    def test_every_cited_page_is_checked_up_to_the_cap(self):
        urls = [f"https://c.io/p{i}" for i in range(8)]
        fetch = FakeFetch({})
        result = run(_details(urls, urls[:2]), fetch=fetch)
        self.assertEqual(len(fetch.calls), 8)
        self.assertEqual(result["pages_cited"], 8)
        self.assertEqual(result["pages_checked"], 8)
        self.assertEqual(result["coverage"], {"checked_citations": 10, "citations_outside_own_site": 10, "ratio": 1.0})

    def test_pages_beyond_the_cap_are_not_fetched_and_lower_the_coverage(self):
        original = source_layers.MAX_PAGES_PER_COMPANY
        source_layers.MAX_PAGES_PER_COMPANY = 3
        try:
            urls = [f"https://c.io/p{i}" for i in range(5)]
            fetch = FakeFetch({})
            result = run(_details(urls, urls[:2]), fetch=fetch)
        finally:
            source_layers.MAX_PAGES_PER_COMPANY = original
        self.assertEqual(len(fetch.calls), 3)
        self.assertTrue({"c.io/p0", "c.io/p1"} <= set(fetch.calls))  # the pages cited twice go first
        layer = by_layer(result)["product_pages"]
        self.assertEqual(layer["pages_not_checked"], 2)
        self.assertEqual(layer["coverage"], {"checked_citations": 5, "citations": 7, "ratio": 0.7143})
        self.assertEqual(result["pages_checked"], 3)

    def test_unknown_pages_do_not_count_as_covered(self):
        layer = by_layer(run(_details(["https://c.io/a", "https://o.io/b"]), {"o.io/b": OSError()}))["product_pages"]
        self.assertEqual(layer["coverage"], {"checked_citations": 1, "citations": 2, "ratio": 0.5})

    def test_own_site_coverage_is_complete_and_a_group_nobody_cited_has_no_ratio(self):
        layers = by_layer(run(_details(["https://acme.com/a", "https://g2.com/b"])))
        self.assertEqual(layers["own_site"]["coverage"]["ratio"], 1.0)
        self.assertIsNone(layers["reference"]["coverage"]["ratio"])

    def test_the_default_cap_is_60(self):
        self.assertEqual(source_layers.MAX_PAGES_PER_COMPANY, 60)


class GapAndConclusionTests(unittest.TestCase):
    def test_gap_is_reliance_times_one_minus_presence(self):
        details = _details(["https://g2.com/a", "https://c.io/best-x", "https://c.io/best-y", "https://c.io/best-z"])
        result = run(details, {"g2.com/a": mention()})
        layers = by_layer(result)
        self.assertAlmostEqual(layers["lists"]["gap"], 0.75)
        self.assertEqual(layers["review_media"]["gap"], 0)
        self.assertEqual(result["biggest_gap_layer"], "lists")
        self.assertEqual(layers["lists"]["status"], "biggest_gap")
        self.assertEqual(layers["review_media"]["status"], "covered")
        self.assertEqual(layers["reference"]["status"], "not_used")

    def test_largest_gap_picks_the_biggest(self):
        gaps = {"reference": Fraction(1, 10), "review_media": Fraction(3, 10), "community": Fraction(2, 10), "guides": None}
        self.assertEqual(source_layers.largest_gap_layer(gaps), "review_media")

    def test_ties_go_to_the_group_first_in_the_fixed_order(self):
        gaps = {"product_pages": Fraction(1, 3), "guides": Fraction(1, 3), "lists": Fraction(1, 3), "review_media": Fraction(1, 6)}
        self.assertEqual(source_layers.largest_gap_layer(gaps), "lists")
        gaps.pop("lists")
        self.assertEqual(source_layers.largest_gap_layer(gaps), "guides")

    def test_no_positive_gap_means_no_biggest_gap(self):
        self.assertIsNone(source_layers.largest_gap_layer({"reference": Fraction(0), "community": None}))
        self.assertIsNone(source_layers.largest_gap_layer({}))

    def test_equal_looking_gaps_tie_exactly_not_by_float_noise(self):
        a, b = Fraction(1, 10) * 3, Fraction(3, 10)
        self.assertEqual(source_layers.largest_gap_layer({"review_media": a, "community": b}), "review_media")

    # ---- conclusion templates, one per page type group
    def test_conclusion_for_lists(self):
        details = _details(["https://a.io/best-crm-tools", "https://a.io/best-crm-tools", "https://b.io/top-10-crms", "https://acme.com/x"])
        pages = {
            "a.io/best-crm-tools": html("<p>none</p>", title="The 12 Best CRM Tools"),
            "b.io/top-10-crms": html("<p>none</p>", title="Top 10 CRMs"),
        }
        self.assertEqual(
            run(details, pages)["conclusion"],
            ["For these questions, AI mostly reads 'best tools' lists and comparisons written by other companies, "
             "and the top ones don't mention you: “The 12 Best CRM Tools” (a.io), “Top 10 CRMs” (b.io)."],
        )

    def test_conclusion_for_guides(self):
        details = _details(["https://x.io/blog/how-to-sell", "https://y.io/docs/crm", "https://z.io/help/a", "https://w.io/learn/b"])
        pages = {"x.io": html("<p>n</p>", title="How to sell"), "y.io": html("<p>n</p>", title="CRM docs")}
        result = run(details, pages)
        self.assertEqual(result["biggest_gap_layer"], "guides")
        self.assertTrue(result["conclusion"][0].startswith(
            "For these questions, AI mostly reads other companies' guides and help articles, and the top ones don't mention you: "))
        self.assertIn("“How to sell” (x.io)", result["conclusion"][0])

    def test_conclusion_for_product_pages_lists_at_most_three_most_cited_pages(self):
        details = _details(
            ["https://a.io/p", "https://b.io/p", "https://c.io/p", "https://d.io/p"],
            ["https://a.io/p", "https://b.io/p"],
            ["https://a.io/p"],
        )
        pages = {u: html("<p>n</p>", title=t) for u, t in [("a.io", "A"), ("b.io", "B"), ("c.io", "C"), ("d.io", "D")]}
        self.assertEqual(
            run(details, pages)["conclusion"][0],
            "For these questions, AI mostly reads other companies' product pages, and the top ones don't mention you: "
            "“A” (a.io), “B” (b.io), “C” (c.io).",
        )

    def test_conclusion_names_the_share_when_some_of_the_pages_mention_you(self):
        details = _details(["https://a.io/best-x", "https://b.io/best-y", "https://c.io/best-z", "https://d.io/best-w"])
        pages = {
            "a.io": mention(title="Mentions you"),
            "b.io": html("<p>n</p>", title="Not Y"),
            "c.io": html("<p>n</p>", title="Not Z"),
            "d.io": html("<p>n</p>", title="Not W"),
        }
        self.assertEqual(
            run(details, pages)["conclusion"][0],
            "For these questions, AI mostly reads 'best tools' lists and comparisons written by other companies, "
            "and only 25% of them mention you. The most cited ones that don't: “Not Y” (b.io), “Not Z” (c.io), “Not W” (d.io).",
        )

    def test_a_missing_title_falls_back_to_the_url(self):
        sentence = run(_details(["https://a.io/best-x"]), {"a.io": html("<p>n</p>", title="")})["conclusion"][0]
        self.assertIn("“a.io/best-x” (a.io)", sentence)

    def test_long_titles_are_shortened(self):
        long_title = "Best " + "word " * 40
        sentence = run(_details(["https://a.io/best-x"]), {"a.io": html("<p>n</p>", title=long_title)})["conclusion"][0]
        self.assertIn("...” (a.io)", sentence)

    def test_conclusion_for_other_groups_keeps_its_template(self):
        details = _details(["https://reddit.com/r/a", "https://youtube.com/v", "https://g2.com/1", "https://g2.com/2", "https://g2.com/3"])
        result = run(details, {"reddit.com": mention("Acme is mentioned"), "g2.com": mention("Acme reviewed")})
        self.assertEqual(result["biggest_gap_layer"], "community")
        self.assertEqual(
            result["conclusion"][0],
            "For these questions, AI often reads community sites such as Reddit, LinkedIn and YouTube, and only 50% of them mention you. "
            "The most cited ones that don't: “Else” (youtube.com).",
        )

    def test_own_site_sentence_follows_when_it_was_never_cited(self):
        self.assertEqual(run(_details(["https://c.io/p"]))["conclusion"][-1], "AI didn't cite your own website in any answer.")

    def test_conclusion_when_the_company_is_everywhere_and_own_site_cited(self):
        result = run(_details(["https://g2.com/a", "https://acme.com/x"]), {"g2.com": mention()})
        self.assertEqual(result["conclusion"], ["For these questions, the websites AI reads do mention you."])

    def test_conclusion_when_nothing_could_be_checked(self):
        result = run(_details(["https://c.io/x", "https://acme.com/y"]), {"c.io": OSError()})
        self.assertEqual(result["conclusion"], ["We couldn't check whether the websites AI reads mention you."])

    def test_conclusion_when_ai_cited_nothing(self):
        result = run(_details([], []))
        self.assertEqual(result["total_citations"], 0)
        self.assertEqual(result["conclusion"], ["AI didn't show any sources for these questions, so we can't tell where it gets its information."])
        self.assertIsNone(result["biggest_gap_layer"])

    def test_result_lists_every_group_in_the_fixed_order_with_domains_and_versions(self):
        result = run(_details(["https://c.io/x", "https://c.io/y", "https://d.io/z"]))
        self.assertEqual([l["layer"] for l in result["layers"]], LAYER_ORDER)
        self.assertEqual(by_layer(result)["product_pages"]["domains"], [{"domain": "c.io", "citations": 2}, {"domain": "d.io", "citations": 1}])
        self.assertEqual((result["list_version"], result["page_type_rules_version"]), ("v2", "v1"))


class DeterminismTests(unittest.TestCase):
    def test_same_input_twice_gives_identical_results(self):
        details = _details(
            ["https://g2.com/a", "https://c.io/best-x", "https://reddit.com/r/a", "https://acme.com/p", "https://en.wikipedia.org/wiki/A"],
            ["https://c.io/best-x", "https://d.io/blog/y", "https://g2.com/b", "https://e.io/product"],
            ["https://c.io/best-x", "https://youtube.com/watch?v=1"],
        )
        pages = {"c.io/best-x": mention(), "g2.com/a": mention(), "d.io": ConnectionError("x")}
        self.assertEqual(run(details, pages), run(details, pages))


if __name__ == "__main__":
    unittest.main()
