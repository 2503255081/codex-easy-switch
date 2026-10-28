"""Run with the desired x86 or x64 Python; requires packaging/requirements.txt."""
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys


root = Path(__file__).resolve().parents[1]
arch = "x64" if struct.calcsize("P") == 8 else "x86"
work = root / "build" / arch
work.mkdir(parents=True, exist_ok=True)
compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe"
bridge = work / "ProviderBridge.exe"
subprocess.run([str(compiler), "/nologo", "/target:exe", "/platform:anycpu", "/reference:System.Web.Extensions.dll",
                "/out:" + str(bridge), str(root / "src/codex_provider_launcher/ProviderBridge.cs")], check=True)
name = "Codex-Provider-Launcher-Windows-" + arch
subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
                "--name", name, "--distpath", str(root / "dist"), "--workpath", str(work / "pyinstaller"),
                "--specpath", str(work), "--paths", str(root / "src"),
                "--hidden-import", "keyring.backends.Windows", "--collect-submodules", "win32ctypes",
                "--copy-metadata", "keyring", "--copy-metadata", "jaraco.classes", "--copy-metadata", "jaraco.context",
                "--copy-metadata", "jaraco.functools", "--copy-metadata", "more-itertools",
                "--copy-metadata", "tomlkit", "--copy-metadata", "filelock", "--copy-metadata", "pywin32-ctypes",
                "--add-data", str(Path(sys.base_prefix) / "LICENSE.txt") + ";licenses/python",
                "--add-data", str(bridge) + ";codex_provider_launcher",
                str(root / "packaging/windows_entry.py")], check=True, cwd=root)
exe = root / "dist" / (name + ".exe")
digest = hashlib.sha256(exe.read_bytes()).hexdigest()
(root / "dist" / (name + ".sha256")).write_text(digest + "  " + exe.name + "\n", encoding="utf-8")
print(exe.name, exe.stat().st_size, digest)
