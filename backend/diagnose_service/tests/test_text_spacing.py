"""Extraction keeps a space between adjacent elements and buttons when asked to (used for the fact check's official text)."""

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from shared import _fetch_utils  # noqa: E402

_FILLER = "<p>" + "This is a long paragraph about our pricing plans and what you get. " * 6 + "</p>"
_PLANS = (
    "<article>" + _FILLER
    + "<p>Solo<span>$19 per user / month</span><span>$9 per user / month</span><button>Try for free</button></p></article>"
)
_PLAN_CARD = (
    "<html><body><div class='plan'><div>Solo</div><div>$19 per user / month</div><div>$9 per user / month</div>"
    "<button>Try for free</button><a href='/x'>Buy now</a></div></body></html>"
)


class TextSpacingTests(unittest.TestCase):
    def test_adjacent_inline_elements_are_not_glued_when_asked(self):
        glued = _fetch_utils.extract_text_from_html(_PLANS)
        spaced = _fetch_utils.extract_text_from_html(_PLANS, separate_blocks=True)
        self.assertIn("Solo$19 per user / month$9 per user / month", glued)  # the old behavior is unchanged
        self.assertIn("Solo $19 per user / month $9 per user / month", spaced)

    def test_button_text_stays_apart_from_its_neighbours(self):
        for method in ("main", "full"):
            text = _fetch_utils.extract_text_from_html(_PLAN_CARD, method=method, separate_blocks=True)
            self.assertIn("$9 per user / month Try for free Buy now", text, method)

    def test_default_output_is_unchanged(self):
        self.assertEqual(
            _fetch_utils.extract_text_from_html(_PLANS),
            _fetch_utils.extract_text_from_html(_PLANS, separate_blocks=False),
        )

    def test_words_are_not_split_by_the_extra_spaces(self):
        html = "<html><body><p>Plans <strong>start</strong> at <em>fifty</em> dollars and up.</p></body></html>"
        self.assertEqual(
            _fetch_utils.extract_text_from_html(html, method="full", separate_blocks=True),
            "Plans start at fifty dollars and up.",
        )


if __name__ == "__main__":
    unittest.main()
