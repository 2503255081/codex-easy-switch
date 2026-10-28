import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack

from codex_provider_launcher import platforms


@unittest.skipIf(os.name == "nt", "POSIX bridge integration")
class PosixLauncherTests(unittest.TestCase):
    def test_frozen_bridge_is_persistent_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fake = root / "portable app"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
            fake.chmod(0o700)
            with patch.object(platforms, "data_dir", return_value=root / "data"), \
                 patch.object(sys, "executable", str(fake)), patch.object(sys, "frozen", True, create=True):
                bridge = platforms.posix_bridge()
            result = subprocess.run([str(bridge), "app-server", "--example"], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.splitlines(), ["--bridge", "app-server", "--example"])
            self.assertEqual(bridge.stat().st_mode & 0o777, 0o700)

    def test_cli_found_without_finder_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            binary = root / ".local/bin/codex"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(0o700)
            with patch.object(Path, "home", return_value=root), patch.object(platforms.shutil, "which", return_value=None):
                self.assertEqual(platforms.find_posix_cli(), binary.resolve())

    def test_packaged_linux_does_not_inject_frozen_library_path(self):
        from codex_provider_launcher.probe import clean_env
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "platform", "linux"), \
             patch.dict(os.environ, {"LD_LIBRARY_PATH": "/launcher-extracted", "LD_LIBRARY_PATH_ORIG": "/user-library", "_PYI_TEST": "fixture"}):
            env = clean_env()
            self.assertEqual(env["LD_LIBRARY_PATH"], "/user-library")
            self.assertNotIn("_PYI_TEST", env)

    def test_official_desktop_bundle_cli_is_preferred(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            desktop = root / "chatgpt"
            cli = root / "resources" / "codex"
            cli.parent.mkdir()
            desktop.write_text("#!/bin/sh\n")
            cli.write_text("#!/bin/sh\n")
            desktop.chmod(0o700)
            cli.chmod(0o700)
            self.assertEqual(platforms.find_posix_cli(desktop), cli.resolve())

    def test_linux_desktop_receives_bridge_and_key(self):
        from codex_provider_launcher import launch
        from codex_provider_launcher.profiles import new_profile
        with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
            root = Path(d)
            cli = root / "codex"
            desktop = root / "chatgpt"
            cli.touch()
            desktop.touch()
            home = root / "codex-home"
            home.mkdir()
            (home / "config.toml").write_text('model="personal-model"\n')
            profile = new_profile("test", "responses", "https://example.com/v1", "auto")
            stack.enter_context(patch.dict(os.environ, {"CODEX_HOME": str(home)}))
            stack.enter_context(patch.object(sys, "platform", "linux"))
            for name, value in {"is_desktop_running": False, "find_linux_desktop": desktop,
                                "find_posix_cli": cli, "posix_bridge": root / "bridge",
                                "data_dir": root / "data", "validate": {},
                                "list_models": ([("sample-model", {})], False)}.items():
                stack.enter_context(patch.object(launch, name, return_value=value))
            stack.enter_context(patch.object(launch.CredentialStore, "get", return_value="fixture-key"))
            popen = stack.enter_context(patch.object(launch.subprocess, "Popen"))
            launch.launch(profile, "", "", "")
            self.assertEqual(popen.call_args.args[0], [str(desktop)])
            self.assertEqual(popen.call_args.kwargs["env"]["CODEX_CLI_PATH"], str(root / "bridge"))
            self.assertEqual(popen.call_args.kwargs["env"]["CODEX_LAUNCHER_API_KEY"], "fixture-key")
