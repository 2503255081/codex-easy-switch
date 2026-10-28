import json
import tempfile
import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codex_provider_launcher import catalog
from codex_provider_launcher.config import candidate
from codex_provider_launcher.profiles import new_profile


class CatalogTests(unittest.TestCase):
    def test_deepseek_catalog_uses_current_schema_template(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "models_cache.json").write_text(json.dumps({"models": [{"slug": "native", "visibility": "list", "truncation_policy": {"mode": "tokens", "limit": 1}, "experimental_supported_tools": []}]}))
            with patch.dict("os.environ", {"CODEX_HOME": str(root)}), patch.object(catalog, "data_dir", return_value=root / "launcher"):
                path, models = catalog.deepseek_catalog()
            self.assertEqual([m["slug"] for m in models], ["deepseek-flash", "deepseek-v4-pro"])
            self.assertEqual(models[0]["input_modalities"], ["text", "image"])
            self.assertEqual(models[0]["service_tiers"], [])
            self.assertIn("truncation_policy", json.loads(path.read_text(encoding="utf-8"))["models"][0])

    def test_model_discovery_filters_hidden_and_fast_models(self):
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                observed.append(self.headers.get("Authorization"))
                raw = json.dumps({"models": [
                    {"slug": "hidden", "visibility": "hide"},
                    {"slug": "model--fast", "visibility": "list"},
                    {"slug": "model", "visibility": "list"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            profile = new_profile("test", "responses", f"http://127.0.0.1:{server.server_port}/v1", "auto")
            with patch.object(catalog.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="codex-cli 1.0")):
                self.assertEqual(catalog.discover(profile, "fixture-only-key", Path("cli")), ("model", True))
            self.assertEqual(observed, ["Bearer fixture-only-key"])
        finally:
            server.shutdown()
            server.server_close()

    def test_checks_visibility_and_fast_duplicates(self):
        models = [{"slug": "model", "visibility": "list"}, {"slug": "hidden", "visibility": "hide"}]
        catalog.verify(models, {3: {"data": [{"id": "model", "hidden": False}, {"id": "hidden", "hidden": True}]}})
        with self.assertRaises(ValueError):
            catalog.verify(models, {3: {"data": [{"id": "model", "hidden": False}, {"id": "hidden", "hidden": False}]}})

    def test_fast_advertisement_must_be_selectable_in_codex(self):
        models = [{"slug": "model", "visibility": "list", "additional_speed_tiers": ["fast"],
                   "service_tiers": [{"id": "priority", "name": "Fast"}]}]
        good = {3: {"data": [{"id": "model", "hidden": False, "serviceTiers": [{"id": "priority", "name": "Fast"}]}]}}
        catalog.verify(models, good)
        lost = {3: {"data": [{"id": "model", "hidden": False, "serviceTiers": []}]}}
        with self.assertRaisesRegex(ValueError, "Fast"):
            catalog.verify(models, lost)

    def test_external_catalog_is_only_isolated_when_requested(self):
        raw = b'model_catalog_json = "old.json"\n[projects."/project"]\ntrust_level="trusted"\n'
        profile = new_profile("test", "responses", "https://example.com/v1", "model")
        with self.assertRaises(ValueError):
            candidate(raw, profile)
        output = candidate(raw, profile, isolate_catalog=True)
        self.assertNotIn(b"model_catalog_json", output)
        self.assertIn(b'trust_level="trusted"', output)
