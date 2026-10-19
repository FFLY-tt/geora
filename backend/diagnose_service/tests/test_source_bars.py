"""Tests for source_bars.py: the bars of "Where AI looked for answers", counted by page."""

import sys
import unittest
from pathlib import Path

_SERVICE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVICE_DIR.parent))
sys.path.insert(0, str(_SERVICE_DIR))

import source_bars  # noqa: E402


def page(url, status="checked", mentions=False, citations=1):
    return {"url": url, "status": status, "mentions_you": mentions if status == "checked" else None, "citations": citations}


def layer(name, *pages):
    return {"layer": name, "pages": list(pages)}


def rows_by_key(block):
    return {row["key"]: row for row in block["rows"]}


class SourceBarsTests(unittest.TestCase):
    def test_no_layers_or_no_pages_means_no_bars(self):
        self.assertIsNone(source_bars.build_source_bars(None))
        self.assertIsNone(source_bars.build_source_bars({"layers": []}))
        self.assertIsNone(source_bars.build_source_bars({"layers": [layer("lists")]}))

    def test_one_page_used_many_times_is_one_page(self):
        pages = [page("a.com/x", citations=1), page("https://www.a.com/x/", citations=4), page("a.com/x#top", citations=2), page("b.com/y")]
        row = rows_by_key(source_bars.build_source_bars({"layers": [layer("lists", *pages)]}))["lists"]
        self.assertEqual(row["pages"], 2)

    def test_a_repeat_keeps_the_best_status_and_one_mention(self):
        pages = [page("a.com/x", status="unknown"), page("a.com/x", mentions=True), page("a.com/x", status="not_checked")]
        row = rows_by_key(source_bars.build_source_bars({"layers": [layer("guides", *pages)]}))["guides"]
        self.assertEqual((row["pages"], row["mention_pages"], row["could_not_open"], row["not_checked"]), (1, 1, 0, 0))

    def test_opened_pages_make_the_bar_and_unopened_ones_are_only_counted(self):
        pages = [
            page("a.com/1", mentions=True),
            page("a.com/2"),
            page("a.com/3"),
            page("a.com/4", status="unknown"),
            page("a.com/5", status="unknown"),
            page("a.com/6", status="not_checked"),
        ]
        row = rows_by_key(source_bars.build_source_bars({"layers": [layer("review_media", *pages)]}))["review_media"]
        self.assertEqual(
            row,
            {"key": "review_media", "name": "Review sites and news", "pages": 3, "mention_pages": 1, "could_not_open": 2, "not_checked": 1},
        )

    def test_a_kind_where_nothing_could_be_opened_has_a_row_with_no_pages(self):
        block = source_bars.build_source_bars({"layers": [layer("community", page("r.com/1", status="unknown")), layer("lists", page("a.com/1"))]})
        community = rows_by_key(block)["community"]
        self.assertEqual((community["pages"], community["could_not_open"]), (0, 1))
        self.assertEqual([row["key"] for row in block["rows"]], ["lists", "community"])

    def test_a_kind_with_no_page_has_no_row_and_the_two_hidden_kinds_never_appear(self):
        block = source_bars.build_source_bars(
            {
                "layers": [
                    layer("lists", page("a.com/1")),
                    layer("guides"),
                    layer("product_pages", page("p.com/1"), page("p.com/2")),
                    layer("reference", page("wikipedia.org/x")),
                ]
            }
        )
        self.assertEqual([row["key"] for row in block["rows"]], ["lists"])

    def test_your_own_website_is_all_blue(self):
        block = source_bars.build_source_bars({"layers": [layer("own_site", page("me.com/", status="cited"), page("me.com/pricing", status="cited"))]})
        row = rows_by_key(block)["own_site"]
        self.assertEqual((row["name"], row["pages"], row["mention_pages"]), ("Your own website", 2, 2))

    def test_rows_are_sorted_by_pages_with_ties_in_the_fixed_order(self):
        layers = [
            layer("community", page("r.com/1"), page("r.com/2")),
            layer("guides", page("g.com/1"), page("g.com/2")),
            layer("lists", page("l.com/1"), page("l.com/2"), page("l.com/3")),
            layer("review_media", page("m.com/1")),
        ]
        block = source_bars.build_source_bars({"layers": layers})
        self.assertEqual([row["key"] for row in block["rows"]], ["lists", "guides", "community", "review_media"])
        self.assertEqual(block["max_pages"], 3)

    def test_most_missing_is_the_kind_with_the_most_pages_that_do_not_mention_you(self):
        layers = [
            layer("lists", page("l.com/1", mentions=True), page("l.com/2"), page("l.com/3")),  # 2 missing
            layer("guides", page("g.com/1"), page("g.com/2"), page("g.com/3"), page("g.com/4", mentions=True)),  # 3 missing
            layer("review_media", page("m.com/1")),
        ]
        self.assertEqual(source_bars.build_source_bars({"layers": layers})["most_missing"], "guides")

    def test_your_own_website_never_counts_as_missing(self):
        layers = [layer("own_site", *[page(f"me.com/{i}", status="cited") for i in range(9)]), layer("lists", page("l.com/1", mentions=True))]
        self.assertIsNone(source_bars.build_source_bars({"layers": layers})["most_missing"])

    def test_when_every_kind_mentions_you_nothing_is_missing(self):
        layers = [layer("lists", page("l.com/1", mentions=True)), layer("guides", page("g.com/1", mentions=True))]
        self.assertIsNone(source_bars.build_source_bars({"layers": layers})["most_missing"])

    def test_a_tie_goes_to_the_first_kind(self):
        layers = [layer("guides", page("g.com/1")), layer("lists", page("l.com/1"))]
        self.assertEqual(source_bars.build_source_bars({"layers": layers})["most_missing"], "lists")

    def test_same_input_twice_gives_identical_bars(self):
        layers = {"layers": [layer("lists", page("a.com/1"), page("a.com/2", mentions=True)), layer("guides", page("g.com/1", status="unknown"))]}
        self.assertEqual(source_bars.build_source_bars(layers), source_bars.build_source_bars(layers))


if __name__ == "__main__":
    unittest.main()
