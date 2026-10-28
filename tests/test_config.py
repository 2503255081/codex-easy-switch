import tempfile
import unittest
from pathlib import Path

import tomlkit

from codex_provider_launcher.config import candidate, commit, restore_last
from codex_provider_launcher.profiles import new_profile


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.profile = new_profile("Relay", "responses", "https://example.com/v1", "example-model")

    def test_preserves_projects_and_other_settings(self):
        raw = b'model = "old"\n[projects."/some/project"]\ntrust_level = "trusted"\n[mcp_servers.test]\ncommand = "example"\n'
        doc = tomlkit.parse(candidate(raw, self.profile).decode())
        self.assertEqual(doc["projects"]["/some/project"]["trust_level"], "trusted")
        self.assertEqual(doc["mcp_servers"]["test"]["command"], "example")
        self.assertEqual(doc["model_providers"]["launcher_custom"]["env_key"], "CODEX_LAUNCHER_API_KEY")

    def test_refuses_conflicting_configuration(self):
        for raw in (b'profile = "old"\n', b'model_catalog_json = "old.json"\n', b'[model_providers.openai]\nname = "old"\n'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                candidate(raw, self.profile)

    def test_backup_and_concurrent_edit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "config.toml"
            original = b'model = "old"\n'
            path.write_bytes(original)
            updated = candidate(original, self.profile)
            backup = commit(path, original, updated, root / "backups", lambda _: None)
            self.assertEqual(backup.read_bytes(), original)
            path.write_bytes(b'new user edit')
            with self.assertRaises(RuntimeError):
                commit(path, updated, original, root / "backups", lambda _: None)
            self.assertEqual(path.read_bytes(), b'new user edit')

    def test_validation_failure_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "config.toml"
            original = b'model = "old"\n'
            path.write_bytes(original)
            def reject(_):
                raise ValueError("invalid")
            with self.assertRaises(ValueError):
                commit(path, original, b"bad", root / "backups", reject)
            self.assertEqual(path.read_bytes(), original)

    def test_restore_checks_later_edits(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "config.toml"
            original = b'model = "old"\n'
            path.write_bytes(original)
            updated = candidate(original, self.profile)
            commit(path, original, updated, root / "backups", lambda _: None)
            path.write_bytes(b'user edit')
            with self.assertRaises(ValueError):
                restore_last(path, root / "backups")
            self.assertEqual(path.read_bytes(), b'user edit')
            path.write_bytes(updated)
            restore_last(path, root / "backups")
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
