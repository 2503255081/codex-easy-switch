import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_provider_launcher import catalog
from codex_provider_launcher.config import candidate
from codex_provider_launcher.profiles import new_profile


class CatalogTests(unittest.TestCase):
    def test_checks_visibility_and_fast_duplicates(self):
        models = [{"slug": "model", "visibility": "list"}, {"slug": "hidden", "visibility": "hide"}]
        catalog.verify(models, {3: {"data": [{"id": "model", "hidden": False}, {"id": "hidden", "hidden": True}]}})
        with self.assertRaises(ValueError):
            catalog.verify(models, {3: {"data": [{"id": "model", "hidden": False}, {"id": "hidden", "hidden": False}]}})

    def test_external_catalog_is_only_isolated_when_requested(self):
        raw = b'model_catalog_json = "old.json"\n[projects."/project"]\ntrust_level="trusted"\n'
        profile = new_profile("test", "responses", "https://example.com/v1", "model")
        with self.assertRaises(ValueError):
            candidate(raw, profile)
        output = candidate(raw, profile, isolate_catalog=True)
        self.assertNotIn(b"model_catalog_json", output)
        self.assertIn(b'trust_level="trusted"', output)
