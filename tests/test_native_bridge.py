import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_provider_launcher.platforms import ensure_windows_bridge


@unittest.skipUnless(os.name == "nt", "native bridge test is Windows-only")
class NativeBridgeTests(unittest.TestCase):
    def test_resume_request_is_forwarded_with_selected_provider(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "target.cs"
            target = root / "target.exe"
            source.write_text('using System; public class Target { public static void Main() { string s; while ((s=Console.ReadLine())!=null) Console.WriteLine(s); } }')
            compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
            subprocess.run([str(compiler), "/nologo", "/target:exe", "/out:" + str(target), str(source)],
                           check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            with patch("codex_provider_launcher.platforms.data_dir", return_value=root):
                bridge = ensure_windows_bridge()
            env = os.environ.copy()
            env.update(CODEX_SWITCH_REAL_CLI=str(target), CODEX_SWITCH_PROVIDER="launcher_custom")
            env.pop("CODEX_SWITCH_LOG", None)
            line = {"id": 1, "method": "thread/resume", "params": {"threadId": "fixture", "modelProvider": "old"}}
            result = subprocess.run([str(bridge), "app-server"], input=json.dumps(line) + "\n", text=True,
                                    capture_output=True, timeout=20, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            message = json.loads(result.stdout)
            self.assertEqual(message["params"]["threadId"], "fixture")
            self.assertEqual(message["params"]["modelProvider"], "launcher_custom")

    def test_resume_and_turn_fixed_model(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "target.cs"
            target = root / "target.exe"
            source.write_text('using System; public class Target { public static void Main() { string s; while ((s=Console.ReadLine())!=null) Console.WriteLine(s); } }')
            compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
            subprocess.run([str(compiler), "/nologo", "/target:exe", "/out:" + str(target), str(source)], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            with patch("codex_provider_launcher.platforms.data_dir", return_value=root):
                bridge = ensure_windows_bridge()
            env = os.environ.copy()
            env.update(CODEX_SWITCH_REAL_CLI=str(target), CODEX_SWITCH_PROVIDER="launcher_custom", CODEX_SWITCH_FIXED_MODEL="deepseek-chat")
            env.pop("CODEX_SWITCH_LOG", None)
            lines = [{"id": i, "method": method, "params": {"model": "gpt-6.1-sol"}}
                     for i, method in enumerate(("thread/resume", "turn/start"), 1)]
            result = subprocess.run([str(bridge), "app-server"], input="".join(json.dumps(x)+"\n" for x in lines), text=True, capture_output=True, timeout=20, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([json.loads(line)["params"]["model"] for line in result.stdout.splitlines()], ["deepseek-chat", "deepseek-chat"])
