import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
system = "macOS" if sys.platform == "darwin" else "Linux"
arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}[platform.machine().lower()]
dist = root / "build" / (system + "-" + arch) / "dist"
name = "Codex-Provider-Launcher"
exe = dist / (name + ".app") / "Contents/MacOS" / name if system == "macOS" else dist / name
with tempfile.TemporaryDirectory(prefix="verify-posix-gui-") as directory:
    report = Path(directory) / "result.json"
    result = subprocess.run([str(exe), "--self-test-gui", str(report)], timeout=30)
    data = json.loads(report.read_text()) if report.exists() else {}
    if result.returncode or not data.get("gui"):
        raise RuntimeError("Packaged graphical interface test failed: " + repr(data))
    print("Packaged graphical interface verified:", system, arch)
