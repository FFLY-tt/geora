"""
Tests for the layered diagnosis: domain classification into layers, and
reliance, presence, gap and the conclusion built from them.

Fully offline: page fetches are replaced by a fake, nothing touches the
network and no LLM is involved.
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
from engines._source_layers import LAYER_LIST_VERSION, LAYER_ORDER, classify_host, classify_url  # noqa: E402

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
        ]:
            self.assertEqual(self.layer(url), "community", url)

    def test_own_site_includes_subdomains_and_www(self):
        for url in ["https://acme.com/", "https://www.acme.com/pricing", "https://trust.acme.com/", "https://docs.eu.acme.com/x"]:
            self.assertEqual(self.layer(url), "own_site", url)

    def test_own_site_wins_even_for_a_listed_domain_name(self):
        self.assertEqual(classify_host("blog.g2.com", "g2.com"), "own_site")

    def test_unlisted_sites_are_other_companies(self):
        for url in ["https://www.competitor.io/blog/acme-vs-us", "https://random-blog.net/post", "https://example.org/"]:
            self.assertEqual(self.layer(url), "other_companies", url)

    def test_lookalike_domains_are_not_matched_by_substring(self):
        for url in ["https://notacme.com/", "https://acme.com.evil.io/", "https://myg2.com/", "https://wikipedia.org.fake.com/", "https://redditx.com/"]:
            self.assertEqual(self.layer(url), "other_companies", url)

    def test_url_without_a_host_has_no_layer(self):
        self.assertIsNone(self.layer(""))

    def test_list_version_and_order_are_fixed(self):
        self.assertEqual(LAYER_LIST_VERSION, "v1")
        self.assertEqual(LAYER_ORDER, ["reference", "review_media", "community", "own_site", "other_companies"])


def _details(*answers):
    """citation_visibility details with one successful run per answer (a list of cited URLs)."""
    runs = [{"status": "ok", "all_citations": list(urls)} for urls in answers]
    return {"per_query_results": [{"query": "q", "runs": runs}]}


class FakeFetch:
    """Stand-in for fetch_page_text: {url fragment: text or Exception}; anything else is a page without the company."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url, method="main"):
        self.calls.append((url, method))
        for fragment, value in self.pages.items():
            if fragment in url:
                if isinstance(value, Exception):
                    raise value
                return value
        return "A page about something else entirely."


def run(details, pages=None, name="Acme", domain=OWN):
    return source_layers.build_source_layers(name, domain, details, fetch_text=FakeFetch(pages or {}))


def by_layer(result):
    return {layer["layer"]: layer for layer in result["layers"]}


class RelianceAndPresenceTests(unittest.TestCase):
    def test_reliance_is_the_layers_share_of_all_citations(self):
        details = _details(
            ["https://g2.com/a", "https://competitor.io/x"],
            ["https://g2.com/a", "https://competitor.io/y", "https://competitor.io/z"],
        )
        layers = by_layer(run(details))
        self.assertEqual(layers["review_media"]["citations"], 2)
        self.assertEqual(layers["other_companies"]["citations"], 3)
        self.assertAlmostEqual(layers["review_media"]["reliance"], 0.4)
        self.assertAlmostEqual(layers["other_companies"]["reliance"], 0.6)
        self.assertEqual(layers["reference"]["reliance"], 0)

    def test_a_url_cited_twice_within_one_answer_counts_once(self):
        details = _details(["https://g2.com/a", "https://www.g2.com/a/", "https://g2.com/a#top"])
        self.assertEqual(by_layer(run(details))["review_media"]["citations"], 1)

    def test_failed_answers_are_ignored(self):
        details = {"per_query_results": [{"runs": [{"status": "error"}, {"status": "ok", "all_citations": ["https://g2.com/a"]}]}]}
        self.assertEqual(run(details)["total_answers"], 1)

    def test_presence_is_weighted_by_citations(self):
        # page A cited 3 times mentions Acme, page B cited once does not: 3/4.
        details = _details(
            ["https://competitor.io/a", "https://other.io/b"],
            ["https://competitor.io/a"],
            ["https://competitor.io/a"],
        )
        pages = {"competitor.io/a": "We compare Acme with others.", "other.io/b": "Nothing relevant."}
        layer = by_layer(run(details, pages))["other_companies"]
        self.assertAlmostEqual(layer["presence"], 0.75)
        self.assertEqual({p["url"]: p["mentions_you"] for p in layer["pages"]}, {"competitor.io/a": True, "other.io/b": False})
        self.assertIn("Acme", [p for p in layer["pages"] if p["mentions_you"]][0]["snippet"])

    def test_unknown_pages_are_left_out_of_the_denominator_and_listed(self):
        details = _details(["https://competitor.io/a", "https://other.io/b"], ["https://competitor.io/a"])
        pages = {"competitor.io/a": "Acme is covered here.", "other.io/b": ConnectionError("boom")}
        layer = by_layer(run(details, pages))["other_companies"]
        self.assertEqual(layer["presence"], 1.0)  # 2 of 2 known citations, the unknown one is not counted
        self.assertEqual([p["url"] for p in layer["unknown_pages"]], ["other.io/b"])
        self.assertEqual(layer["unknown_pages"][0]["error"], "ConnectionError")

    def test_a_layer_with_only_unknown_pages_has_no_presence_or_gap(self):
        details = _details(["https://competitor.io/a"])
        layer = by_layer(run(details, {"competitor.io/a": TimeoutError()}))["other_companies"]
        self.assertIsNone(layer["presence"])
        self.assertIsNone(layer["gap"])
        self.assertEqual(layer["status"], "unknown")

    def test_own_site_presence_is_just_whether_it_was_cited(self):
        cited = by_layer(run(_details(["https://acme.com/pricing"], ["https://g2.com/a"])))["own_site"]
        self.assertEqual(cited["presence"], 1.0)
        self.assertEqual(cited["gap"], 0)
        not_cited = by_layer(run(_details(["https://g2.com/a"])))["own_site"]
        self.assertEqual(not_cited["citations"], 0)
        self.assertEqual(not_cited["reliance"], 0)

    def test_own_site_pages_are_never_fetched(self):
        fetch = FakeFetch({})
        source_layers.build_source_layers("Acme", OWN, _details(["https://acme.com/a", "https://g2.com/b"]), fetch_text=fetch)
        self.assertEqual([url for url, _ in fetch.calls], ["https://g2.com/b"])

    def test_pages_are_fetched_with_method_full_and_capped_per_layer(self):
        urls = [f"https://competitor.io/p{i}" for i in range(8)]
        fetch = FakeFetch({})
        source_layers.build_source_layers("Acme", OWN, _details(urls, urls[:2]), fetch_text=fetch)
        self.assertEqual(len(fetch.calls), source_layers.MAX_PAGES_PER_LAYER)
        self.assertTrue(all(method == "full" for _, method in fetch.calls))
        # the two pages cited twice are among the five fetched
        fetched = {url for url, _ in fetch.calls}
        self.assertTrue({"https://competitor.io/p0", "https://competitor.io/p1"} <= fetched)

    def test_company_name_matching_is_whole_word_and_common_words_need_the_domain(self):
        self.assertIsNotNone(source_layers.page_mentions_company("Feroot Security", "feroot.com", "Try Feroot Security today"))
        self.assertIsNone(source_layers.page_mentions_company("Feroot Security", "feroot.com", "Nothing here"))
        self.assertIsNone(source_layers.page_mentions_company("Close", "close.com", "Time to close the deal and go home"))
        self.assertIsNotNone(source_layers.page_mentions_company("Close", "close.com", "Compare tools at close.com for sales"))
        self.assertIsNone(source_layers.page_mentions_company("Close", "close.com", "see enclose.com"))

    def test_no_successful_answers_gives_no_layers(self):
        self.assertIsNone(run({"per_query_results": [{"runs": [{"status": "error"}]}]}))
        self.assertIsNone(run({}))


class GapAndConclusionTests(unittest.TestCase):
    def test_gap_is_reliance_times_one_minus_presence(self):
        # other_companies: 3 of 4 citations, none mention Acme -> gap 0.75. review_media: 1 of 4, mentions -> gap 0.
        details = _details(["https://g2.com/a", "https://c.io/x", "https://c.io/y", "https://c.io/z"])
        result = run(details, {"g2.com/a": "Acme reviews"})
        layers = by_layer(result)
        self.assertAlmostEqual(layers["other_companies"]["gap"], 0.75)
        self.assertEqual(layers["review_media"]["gap"], 0)
        self.assertEqual(result["biggest_gap_layer"], "other_companies")
        self.assertEqual(layers["other_companies"]["status"], "biggest_gap")
        self.assertEqual(layers["review_media"]["status"], "covered")
        self.assertEqual(layers["reference"]["status"], "not_used")

    def test_largest_gap_picks_the_biggest(self):
        gaps = {"reference": Fraction(1, 10), "review_media": Fraction(3, 10), "community": Fraction(2, 10), "own_site": Fraction(0), "other_companies": None}
        self.assertEqual(source_layers.largest_gap_layer(gaps), "review_media")

    def test_ties_go_to_the_layer_first_in_the_fixed_order(self):
        gaps = {"other_companies": Fraction(1, 3), "community": Fraction(1, 3), "reference": Fraction(1, 3), "review_media": Fraction(1, 6)}
        self.assertEqual(source_layers.largest_gap_layer(gaps), "reference")
        gaps.pop("reference")
        self.assertEqual(source_layers.largest_gap_layer(gaps), "community")

    def test_no_positive_gap_means_no_biggest_gap(self):
        self.assertIsNone(source_layers.largest_gap_layer({"reference": Fraction(0), "community": None}))
        self.assertIsNone(source_layers.largest_gap_layer({}))

    def test_equal_looking_gaps_tie_exactly_not_by_float_noise(self):
        # 1/10 + 2/10 style float noise must not decide a tie
        a, b = Fraction(1, 10) * 3, Fraction(3, 10)
        self.assertEqual(a, b)
        self.assertEqual(source_layers.largest_gap_layer({"review_media": a, "community": b}), "review_media")

    def test_conclusion_for_other_companies_with_no_mention_and_no_own_site(self):
        details = _details(["https://c.io/x", "https://d.io/y", "https://g2.com/a"])
        result = run(details, {"g2.com/a": "Acme"})
        self.assertEqual(
            result["conclusion"],
            [
                "For these questions, AI mostly reads other companies' websites, and they don't mention you.",
                "AI didn't cite your own website in any answer.",
            ],
        )

    def test_conclusion_uses_often_below_half_and_a_percentage_when_partly_present(self):
        # community 2 of 5 citations (0.4 reliance); 1 of the 2 cited pages mentions Acme.
        details = _details(["https://reddit.com/r/a", "https://youtube.com/v", "https://g2.com/1", "https://g2.com/2", "https://g2.com/3"])
        pages = {"reddit.com": "Acme is mentioned", "g2.com": "Acme reviewed"}
        # make review_media fully present and community half present
        result = run(details, pages)
        self.assertEqual(result["biggest_gap_layer"], "community")
        self.assertEqual(
            result["conclusion"][0],
            "For these questions, AI often reads community sites such as Reddit, LinkedIn and YouTube, and only 50% of what it reads there mentions you.",
        )

    def test_conclusion_when_the_company_is_everywhere_and_own_site_cited(self):
        result = run(_details(["https://g2.com/a", "https://acme.com/x"]), {"g2.com": "Acme"})
        self.assertEqual(result["conclusion"], ["For these questions, the websites AI reads do mention you."])

    def test_conclusion_when_nothing_could_be_checked(self):
        result = run(_details(["https://c.io/x", "https://acme.com/y"]), {"c.io": OSError()})
        self.assertEqual(result["conclusion"], ["We couldn't check whether the websites AI reads mention you."])

    def test_conclusion_when_ai_cited_nothing(self):
        result = run(_details([], []))
        self.assertEqual(result["total_citations"], 0)
        self.assertEqual(result["conclusion"], ["AI didn't show any sources for these questions, so we can't tell where it gets its information."])
        self.assertIsNone(result["biggest_gap_layer"])

    def test_result_lists_every_layer_in_the_fixed_order_with_domains(self):
        result = run(_details(["https://c.io/x", "https://c.io/y", "https://d.io/z"]))
        self.assertEqual([l["layer"] for l in result["layers"]], LAYER_ORDER)
        domains = by_layer(result)["other_companies"]["domains"]
        self.assertEqual(domains, [{"domain": "c.io", "citations": 2}, {"domain": "d.io", "citations": 1}])
        self.assertEqual(result["list_version"], LAYER_LIST_VERSION)


class DeterminismTests(unittest.TestCase):
    def test_same_input_twice_gives_identical_results(self):
        details = _details(
            ["https://g2.com/a", "https://c.io/x", "https://reddit.com/r/a", "https://acme.com/p", "https://en.wikipedia.org/wiki/A"],
            ["https://c.io/x", "https://d.io/y", "https://g2.com/b"],
            ["https://c.io/x", "https://youtube.com/watch?v=1"],
        )
        pages = {"c.io/x": "Acme vs others", "g2.com/a": "Acme", "d.io": ConnectionError("x")}
        first = run(details, pages)
        second = run(details, pages)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
