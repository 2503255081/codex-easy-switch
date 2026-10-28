import tempfile
import unittest
from pathlib import Path

from codex_provider_launcher.profiles import ProfileStore, new_profile, normalize_url


class ProfileTests(unittest.TestCase):
    def test_url_policy(self):
        self.assertEqual(normalize_url("http://127.0.0.1:1234/v1/"), "http://127.0.0.1:1234/v1")
        for value in ("http://example.com/v1", "https://user:password@example.com", "https://example.com/v1?key=x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_url(value)

    def test_roundtrip_and_duplicate_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            store = ProfileStore(Path(d))
            p = new_profile("Test", "responses", "https://example.com/v1", "model")
            store.save([p])
            self.assertEqual(store.load(), [p])
            with self.assertRaises(ValueError):
                store.save([p, p])
            self.assertNotIn("fixture-only-key", store.path.read_text())
