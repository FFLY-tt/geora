"""
Tests for engines._judgment_cache.

Pure file-based cache logic: no network, no mocking needed beyond
redirecting the shared state directory to a temp path per test, so this runs the
same locally and in CI.
"""

import json
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
import engines._state_store as state_store  # noqa: E402


class JudgmentCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp_dir.name)
        self._patches = [
            patch.object(state_store, "STATE_DIR", tmp_path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp_dir.cleanup()

    def test_miss_on_empty_cache(self):
        key = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")
        self.assertIsNone(judgment_cache.get_cached_judgment("evidence_density", key))

    def test_round_trip_save_and_get(self):
        key = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")
        judgment_cache.save_judgment("evidence_density", key, {"score": 55, "items": []})

        cached = judgment_cache.get_cached_judgment("evidence_density", key)

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

    def _write_engine_file(self, namespace, content):
        path = state_store.STATE_DIR / "judgments" / f"{namespace}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_corrupted_cache_file_treated_as_miss_not_raised(self):
        self._write_engine_file("evidence_density", "not valid json{{{")

        key = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")

        self.assertIsNone(judgment_cache.get_cached_judgment("evidence_density", key))

    def test_save_never_overwrites_a_corrupted_shared_file(self):
        # Shared, committed state: a broken file (e.g. a bad merge) is left
        # for a human to fix, never silently replaced.
        path = self._write_engine_file("evidence_density", "not valid json{{{")

        key = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")
        judgment_cache.save_judgment("evidence_density", key, {"score": 10})

        self.assertEqual(path.read_text(encoding="utf-8"), "not valid json{{{")

    def test_one_file_per_engine_under_state_judgments(self):
        key_a = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "x")
        key_b = judgment_cache.make_judgment_cache_key("topic_relevance", "v1", "deepseek-chat", "x")
        judgment_cache.save_judgment("evidence_density", key_a, {"score": 1})
        judgment_cache.save_judgment("topic_relevance", key_b, {"classification": "missing"})

        files = sorted(p.name for p in (state_store.STATE_DIR / "judgments").glob("*.json"))
        self.assertEqual(files, ["evidence_density.json", "topic_relevance.json"])
        self.assertIsNone(judgment_cache.get_cached_judgment("topic_relevance", key_a))

    def test_file_is_written_with_sorted_keys(self):
        keys = [judgment_cache.make_judgment_cache_key("evidence_density", "v1", "m", t) for t in ("a", "b", "c", "d")]
        for key in keys:
            judgment_cache.save_judgment("evidence_density", key, {"score": 1})

        raw = (state_store.STATE_DIR / "judgments" / "evidence_density.json").read_text(encoding="utf-8")
        self.assertEqual(list(json.loads(raw).keys()), sorted(keys))
        self.assertEqual(raw, state_store.dump_state_json(json.loads(raw)))

    def test_existing_judgment_is_never_overwritten(self):
        key = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "some text")
        judgment_cache.save_judgment("evidence_density", key, {"score": 10})
        judgment_cache.save_judgment("evidence_density", key, {"score": 99})

        self.assertEqual(judgment_cache.get_cached_judgment("evidence_density", key), {"score": 10})

    def test_multiple_entries_coexist(self):
        key1 = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "page A")
        key2 = judgment_cache.make_judgment_cache_key("evidence_density", "v1", "deepseek-chat", "page B")
        judgment_cache.save_judgment("evidence_density", key1, {"score": 10})
        judgment_cache.save_judgment("evidence_density", key2, {"score": 90})

        self.assertEqual(judgment_cache.get_cached_judgment("evidence_density", key1), {"score": 10})
        self.assertEqual(judgment_cache.get_cached_judgment("evidence_density", key2), {"score": 90})


if __name__ == "__main__":
    unittest.main()
