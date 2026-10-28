import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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
