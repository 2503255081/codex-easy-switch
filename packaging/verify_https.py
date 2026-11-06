"""Verify the actual portable binary can validate public HTTPS certificates."""
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
name = "Codex-Provider-Launcher"
if sys.platform == "win32":
    arch = "x64" if struct.calcsize("P") == 8 else "x86"
    exe = root / "dist" / f"{name}-Windows-{arch}.exe"
else:
    system = "macOS" if sys.platform == "darwin" else "Linux"
    arch = {"x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}[platform.machine().lower()]
    dist = root / "build" / f"{system}-{arch}" / "dist"
    exe = dist / f"{name}.app" / "Contents/MacOS" / name if system == "macOS" else dist / name
# No builder Python/CA environment, no keys, and no real user's gateway are used.
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(
    ("PYTHON", "CODEX", "OPENAI", "OPENCODEX", "DEEPSEEK", "SSL_CERT", "REQUESTS_CA", "CURL_CA", "TCL_LIBRARY", "TK_LIBRARY"))}
with tempfile.TemporaryDirectory(prefix="verify-launcher-https-") as directory:
    report = Path(directory) / "result.json"
    result = subprocess.run([str(exe), "--self-test-https", str(report)], env=env, cwd=directory, timeout=45)
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    assert result.returncode == 0 and data.get("https") and data.get("certificate_verification") and data.get("bundled_ca"), data
    print(json.dumps(data))
