import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_provider_launcher.probe import validate


@unittest.skipUnless(os.name == "nt", "Windows inherited pipe and process-tree regression")
class ProbeProcessTests(unittest.TestCase):
    def test_inherited_stdout_child_is_stopped_and_probe_directory_removed(self):
        source = r'''
using System;
using System.IO;
using System.Diagnostics;
using System.Threading;
class Server {
  static void Main(string[] args) {
    if (args.Length > 0 && args[0] == "child") {
      Directory.SetCurrentDirectory(Environment.GetEnvironmentVariable("CODEX_HOME"));
      File.WriteAllText(Environment.GetEnvironmentVariable("PROBE_TEST_CHILD"), Process.GetCurrentProcess().Id.ToString());
      Thread.Sleep(60000); return;
    }
    Process.Start(new ProcessStartInfo(Process.GetCurrentProcess().MainModule.FileName,"child") {
      UseShellExecute=false, CreateNoWindow=true
    });
    for (int i=0; i<100 && !File.Exists(Environment.GetEnvironmentVariable("PROBE_TEST_CHILD")); i++) Thread.Sleep(20);
    string s;
    while ((s=Console.ReadLine()) != null) {
      if (s.Contains("\"id\":1")) Console.WriteLine("{\"id\":1,\"result\":{}}");
      if (s.Contains("\"id\":2")) Console.WriteLine("{\"id\":2,\"result\":{}}");
      if (s.Contains("\"id\":3")) Console.WriteLine("{\"id\":3,\"result\":{\"data\":[{\"id\":\"fixture\"}]}}");
    }
  }
}'''
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cs, exe, child = root / "server.cs", root / "server.exe", root / "child.pid"
            cs.write_text(source)
            compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
            subprocess.run([str(compiler), "/nologo", "/out:" + str(exe), str(cs)], check=True,
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            with patch.dict(os.environ, {"PROBE_TEST_CHILD": str(child), "TEMP": d, "TMP": d}):
                start = time.monotonic()
                result = validate(exe, b"", timeout=8)
                self.assertLess(time.monotonic() - start, 10)
                self.assertEqual(result[3]["data"][0]["id"], "fixture")
                self.assertFalse(list(root.glob("codex-launcher-probe-*")))
            # The result is only meaningful if the inheriting child actually started.
            self.assertTrue(child.exists())
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, int(child.read_text()))
            if handle:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 1000), 0)
                finally:
                    kernel.CloseHandle(handle)
