"""Check the build architecture and run the EXE without Python on PATH."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
arch = "x64" if struct.calcsize("P") == 8 else "x86"
exe = root / "dist" / ("Codex-Provider-Launcher-Windows-" + arch + ".exe")
raw = exe.read_bytes()
pe_offset = struct.unpack_from("<I", raw, 0x3C)[0]
assert struct.unpack_from("<H", raw, pe_offset + 4)[0] == (0x8664 if arch == "x64" else 0x14C)
env = {k: v for k, v in os.environ.items() if not any(k.upper().startswith(p) for p in
       ("PYTHON", "CONDA", "CODEX", "OPENAI", "LAUNCHER", "OPENCODEX", "DEEPSEEK"))}
env["PATH"] = os.environ["WINDIR"] + "\\System32;" + os.environ["WINDIR"]
with tempfile.TemporaryDirectory(prefix="launcher-verify-") as d:
    report = Path(d) / "result.json"
    result = subprocess.run([str(exe), "--self-test", str(report)], env=env, cwd=d, timeout=45)
    data = json.loads(report.read_text(encoding="utf-8"))
    assert result.returncode == 0 and data["ok"], data
    assert data["launcher_bits"] == struct.calcsize("P") * 8
    print(json.dumps(data))
assert hashlib.sha256(raw).hexdigest() == exe.with_suffix(".sha256").read_text().split()[0]
print("SHA256 and executable architecture verified:", exe.name)
