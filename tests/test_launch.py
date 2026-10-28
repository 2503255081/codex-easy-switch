import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tomlkit
from codex_provider_launcher import launch
from codex_provider_launcher.profiles import new_profile


@unittest.skipUnless(os.name == "nt", "desktop launch orchestration mock currently covers Windows")
class LaunchTests(unittest.TestCase):
    def test_personal_login_detection_handles_missing_and_api_key_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertFalse(launch.has_personal_auth(root))
            path = root / "auth.json"
            path.write_text(json.dumps({"auth_mode": "apikey"}))
            self.assertFalse(launch.has_personal_auth(root))
            path.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "fixture"}}))
            self.assertTrue(launch.has_personal_auth(root))

    def test_personal_relay_roundtrip_preserves_auth_and_projects(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            home, data = root / "codex", root / "data"
            home.mkdir()
            config = home / "config.toml"
            auth = home / "auth.json"
            auth.write_bytes(b'fixture-only-auth')
            config.write_text('model_provider="openai"\nmodel="personal-model"\n[projects."c:/fixture"]\ntrust_level="trusted"\n')
            profile = new_profile("fixture", "responses", "https://example.com/v1", "relay-model")
            captured = []
            def fake_popen(command, **kwargs):
                captured.append(kwargs["env"])
                return object()
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}), \
                 patch.object(launch, "is_desktop_running", return_value=False), \
                 patch.object(launch, "windows_runtime", return_value=(root / "desktop.exe", root / "cli.exe")), \
                 patch.object(launch, "ensure_windows_bridge", return_value=root / "bridge.exe"), \
                 patch.object(launch, "data_dir", return_value=data), \
                 patch.object(launch, "validate"), \
                 patch.object(launch.CredentialStore, "get", return_value="fixture-only-key"), \
                 patch.object(launch.subprocess, "Popen", side_effect=fake_popen):
                launch.launch(profile, "", "", "")
                custom = tomlkit.parse(config.read_text())
                self.assertEqual(custom["model_provider"], "launcher_custom")
                self.assertEqual(captured[-1]["CODEX_LAUNCHER_API_KEY"], "fixture-only-key")
                launch.launch(None, "", "", "")
                personal = tomlkit.parse(config.read_text())
                self.assertEqual(personal["model_provider"], "openai")
                self.assertEqual(personal["model"], "personal-model")
                self.assertNotIn("CODEX_LAUNCHER_API_KEY", captured[-1])
                self.assertEqual(personal["projects"]["c:/fixture"]["trust_level"], "trusted")
                self.assertEqual(auth.read_bytes(), b'fixture-only-auth')

    def test_legacy_deepseek_uses_native_api_without_chat_adapter(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
            root = Path(d)
            (root / "config.toml").write_text('model="personal-model"\n')
            legacy = new_profile("DeepSeek", "deepseek", "https://api.deepseek.com", "deepseek-chat")
            stack.enter_context(patch.dict(os.environ, {"CODEX_HOME": str(root)}))
            for name, value in {"is_desktop_running": False, "windows_runtime": (root / "app", root / "cli"),
                                "ensure_windows_bridge": root / "bridge", "data_dir": root / "data",
                                "deepseek_catalog": (root / "catalog.json", None), "validate": {}}.items():
                stack.enter_context(patch.object(launch, name, return_value=value))
            stack.enter_context(patch.object(launch.CredentialStore, "get", return_value="fixture-key"))
            chat = stack.enter_context(patch.object(launch, "ChatAdapter"))
            popen = stack.enter_context(patch.object(launch.subprocess, "Popen"))
            _, adapter = launch.launch(legacy, "", "", "")
            chat.assert_not_called()
            self.assertIsNone(adapter)
            config = tomlkit.parse((root / "config.toml").read_text())
            self.assertEqual(config["model"], "deepseek-flash")
            self.assertTrue(set(("none", "low", "high", "max")).issubset(config["desktop"]["enabled-reasoning-efforts"]))
            provider = config["model_providers"]["launcher_custom"]
            self.assertEqual(provider["base_url"], "https://api.deepseek.com")
            self.assertEqual(provider["wire_api"], "responses")
            self.assertEqual(popen.call_args.kwargs["env"]["CODEX_LAUNCHER_API_KEY"], "fixture-key")
            self.assertEqual(popen.call_args.kwargs["env"]["CODEX_SWITCH_FIXED_MODEL"], "deepseek-flash")
