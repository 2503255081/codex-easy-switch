import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codex_provider_launcher import platforms


@unittest.skipUnless(os.name == "nt", "Windows package discovery")
class WindowsRuntimeTests(unittest.TestCase):
    def test_store_update_uses_bundled_cli_until_matching_cache_is_created(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); package = root / "package"
            bundled = package / "app/resources/codex.exe"
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"current official cli")
            app = package / "app/ChatGPT.exe"; app.write_bytes(b"app")
            stale = root / "OpenAI/Codex/bin/old/codex.exe"
            stale.parent.mkdir(parents=True); stale.write_bytes(b"old cli")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root)}), patch.object(
                platforms.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=str(package))
            ):
                self.assertEqual(platforms.windows_runtime(), (app, bundled))
                cached = stale.parent.parent / "new/codex.exe"
                cached.parent.mkdir(); cached.write_bytes(bundled.read_bytes())
                self.assertEqual(platforms.windows_runtime(), (app, cached))

    def test_incomplete_official_package_still_fails(self):
        with tempfile.TemporaryDirectory() as d, patch.object(
            platforms.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=d)
        ):
            with self.assertRaisesRegex(RuntimeError, "不完整"):
                platforms.windows_runtime()
