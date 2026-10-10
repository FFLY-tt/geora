"""
Tests for engines._judgment_cache.

Pure file-based cache logic: no network, no mocking needed beyond
redirecting the cache file to a temp path per test, so this runs the
same locally and in CI.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_THIS_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _THIS_DIR.parent  # backend/diagnose_service
_BACKEND_DIR = _SERVICE_DIR.parent  # backend/

sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SERVICE_DIR))

import engines._judgment_cache as judgment_cache  # noqa: E402


class JudgmentCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp_dir.name)
        self._patches = [
            patch.object(judgment_cache, "_DATA_DIR", tmp_path),
            patch.object(judgment_cache, "_CACHE_FILE", tmp_path / "judgment_cache.json"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp_dir.cleanup()

    def test_miss_on_empty_cache(self):
        key = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")
        self.assertIsNone(judgment_cache.get_cached_judgment(key))

    def test_round_trip_save_and_get(self):
        key = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")
        judgment_cache.save_judgment(key, {"score": 55, "items": []})

        cached = judgment_cache.get_cached_judgment(key)

        self.assertEqual(cached, {"score": 55, "items": []})

    def test_same_namespace_version_model_and_input_produces_same_key(self):
        key1 = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")
        key2 = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")

        self.assertEqual(key1, key2)

    def test_different_input_produces_different_key(self):
        key1 = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")
        key2 = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some textX")

        self.assertNotEqual(key1, key2)

    def test_different_prompt_version_produces_different_key(self):
        key1 = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")
        key2 = judgment_cache.make_judgment_cache_key("evidence_density", "v2", "deepseek-chat", "some text")

        self.assertNotEqual(key1, key2)

    def test_different_namespace_produces_different_key(self):
        key1 = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")
        key2 = judgment_cache.make_judgment_cache_key("topic_relevance", "v1", "deepseek-chat", "some text")

        self.assertNotEqual(key1, key2)

    def test_different_model_produces_different_key(self):
        key1 = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")
        key2 = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-v2", "some text")

        self.assertNotEqual(key1, key2)

    def test_corrupted_cache_file_treated_as_miss_not_raised(self):
        judgment_cache._DATA_DIR.mkdir(parents=True, exist_ok=True)
        judgment_cache._CACHE_FILE.write_text("not valid json{{{", encoding="utf-8")

        key = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")

        self.assertIsNone(judgment_cache.get_cached_judgment(key))

    def test_save_after_corrupted_file_starts_fresh(self):
        judgment_cache._DATA_DIR.mkdir(parents=True, exist_ok=True)
        judgment_cache._CACHE_FILE.write_text("not valid json{{{", encoding="utf-8")

        key = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")
        judgment_cache.save_judgment(key, {"score": 10})

        self.assertEqual(judgment_cache.get_cached_judgment(key), {"score": 10})

    def test_multiple_entries_coexist(self):
        key1 = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "page A")
        key2 = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "page B")
        judgment_cache.save_judgment(key1, {"score": 10})
        judgment_cache.save_judgment(key2, {"score": 90})

        self.assertEqual(judgment_cache.get_cached_judgment(key1), {"score": 10})
        self.assertEqual(judgment_cache.get_cached_judgment(key2), {"score": 90})


if __name__ == "__main__":
    unittest.main()
