"""Tests for radar.py: the five-corner chart numbers built from the layered diagnosis."""

import sys
import unittest
from pathlib import Path

_SERVICE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVICE_DIR.parent))
sys.path.insert(0, str(_SERVICE_DIR))

import radar  # noqa: E402


def layer(name, citations, reliance, presence=None):
    return {"layer": name, "citations": citations, "reliance": reliance, "presence": presence}


def layers_result(*layers, total=100):
    return {"total_citations": total, "layers": list(layers)}


EXAMPLE = layers_result(
    layer("reference", 0, 0.0),
    layer("review_media", 13, 0.0867, 0.25),
    layer("community", 0, 0.0),
    layer("own_site", 2, 0.0133, 1.0),
    layer("lists", 88, 0.5867, 0.2881),
    layer("guides", 34, 0.2267, 0.1429),
    layer("product_pages", 13, 0.0867, None),
    total=150,
)


def axes_by_key(block):
    return {axis["key"]: axis for axis in block["axes"]}


class RadarTests(unittest.TestCase):
    def test_no_layers_means_no_chart(self):
        self.assertIsNone(radar.build_radar(None))
        self.assertIsNone(radar.build_radar({}))

    def test_five_corners_in_the_fixed_order(self):
        block = radar.build_radar(EXAMPLE)
        self.assertEqual([a["key"] for a in block["axes"]], ["lists", "guides", "review_media", "community", "own_site"])
        self.assertEqual(block["total_sources"], 150)

    def test_gray_is_the_share_of_all_sources_and_blue_is_gray_times_the_share_that_mentions_you(self):
        axes = axes_by_key(radar.build_radar(EXAMPLE))
        self.assertEqual((axes["lists"]["gray"], axes["lists"]["blue"]), (58.7, round(58.7 * 0.2881, 1)))
        self.assertEqual((axes["guides"]["gray"], axes["guides"]["blue"]), (22.7, round(22.7 * 0.1429, 1)))
        self.assertEqual((axes["review_media"]["gray"], axes["review_media"]["blue"]), (8.7, round(8.7 * 0.25, 1)))

    def test_blue_never_exceeds_gray(self):
        for presence in (0.0, 0.3, 0.9999, 1.0):
            block = radar.build_radar(layers_result(layer("lists", 40, 0.4, presence), layer("guides", 10, 0.1, presence)))
            for axis in block["axes"]:
                self.assertLessEqual(axis["blue"], axis["gray"])

    def test_your_website_blue_equals_gray(self):
        axes = axes_by_key(radar.build_radar(EXAMPLE))
        self.assertEqual(axes["own_site"]["gray"], 1.3)
        self.assertEqual(axes["own_site"]["blue"], axes["own_site"]["gray"])

    def test_a_kind_of_website_ai_never_used_is_zero_and_not_used(self):
        axes = axes_by_key(radar.build_radar(EXAMPLE))
        self.assertEqual(axes["community"], {"key": "community", "gray": 0.0, "blue": 0.0, "status": "not_used"})
        missing = radar.build_radar(layers_result(layer("lists", 5, 1.0, 0.5)))  # corners the data does not list at all
        self.assertEqual(axes_by_key(missing)["guides"]["status"], "not_used")

    def test_pages_that_could_not_be_opened_give_blue_zero_and_could_not_check(self):
        block = radar.build_radar(layers_result(layer("community", 6, 0.12, None), layer("lists", 20, 0.4, 0.5)))
        community = axes_by_key(block)["community"]
        self.assertEqual((community["gray"], community["blue"], community["status"]), (12.0, 0.0, "could_not_check"))

    def test_the_scale_is_the_largest_gray_rounded_up_to_a_multiple_of_ten(self):
        self.assertEqual(radar.build_radar(EXAMPLE)["scale_max"], 60)  # 58.7
        self.assertEqual(radar.build_radar(layers_result(layer("lists", 5, 0.5, 0.1)))["scale_max"], 50)  # exactly 50
        self.assertEqual(radar.build_radar(layers_result(layer("lists", 5, 0.501, 0.1)))["scale_max"], 60)  # 50.1
        self.assertEqual(radar.build_radar(layers_result(layer("lists", 5, 0.03, 0.1)))["scale_max"], 10)
        self.assertEqual(radar.build_radar(layers_result(layer("lists", 0, 0.0)))["scale_max"], 10)  # nothing used at all
        self.assertEqual(radar.build_radar(layers_result(layer("lists", 5, 1.0, 0.1)))["scale_max"], 100)

    def test_the_hidden_kinds_still_count_in_the_denominator_but_have_no_corner(self):
        block = radar.build_radar(EXAMPLE)
        self.assertEqual(sum(a["gray"] for a in block["axes"]), 91.4)  # product_pages 8.7 is left out of the corners
        self.assertNotIn("product_pages", [a["key"] for a in block["axes"]])
        self.assertNotIn("reference", [a["key"] for a in block["axes"]])

    def test_the_biggest_gap_is_gray_minus_blue_among_corners_that_could_be_checked(self):
        self.assertEqual(radar.build_radar(EXAMPLE)["biggest_gap"], "lists")
        unreadable = radar.build_radar(layers_result(layer("community", 60, 0.6, None), layer("guides", 20, 0.2, 0.5)))
        self.assertEqual(unreadable["biggest_gap"], "guides")  # unreadable pages are not counted as missing
        self.assertIsNone(radar.build_radar(layers_result(layer("lists", 10, 0.5, 1.0)))["biggest_gap"])

    def test_a_tie_goes_to_the_first_corner(self):
        block = radar.build_radar(layers_result(layer("guides", 20, 0.2, 0.0), layer("lists", 20, 0.2, 0.0)))
        self.assertEqual(block["biggest_gap"], "lists")

    def test_same_input_twice_gives_identical_numbers(self):
        self.assertEqual(radar.build_radar(EXAMPLE), radar.build_radar(EXAMPLE))


if __name__ == "__main__":
    unittest.main()
