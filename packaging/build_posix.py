"""Build a portable GUI on Linux or an app bundle on macOS; run on target OS."""
import hashlib
import os
import platform
from pathlib import Path
import subprocess
import sys
import tarfile

root = Path(__file__).resolve().parents[1]
system = "macOS" if sys.platform == "darwin" else "Linux"
machine = platform.machine().lower()
arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
if not arch or sys.platform not in ("linux", "darwin"):
    raise SystemExit("Unsupported build platform: " + sys.platform + " " + machine)
work = root / "build" / (system + "-" + arch)
dist = work / "dist"
work.mkdir(parents=True, exist_ok=True)
name = "Codex-Provider-Launcher"
args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", name,
        "--distpath", str(dist), "--workpath", str(work / "work"), "--specpath", str(work), "--paths", str(root / "src"),
        "--hidden-import", "codex_provider_launcher.bridge", "--copy-metadata", "keyring", "--copy-metadata", "tomlkit",
        "--copy-metadata", "filelock", "--copy-metadata", "jaraco.classes", "--copy-metadata", "jaraco.context",
        "--copy-metadata", "jaraco.functools", "--copy-metadata", "more-itertools"]
if system == "macOS":
    args += ["--onedir", "--hidden-import", "keyring.backends.macOS"]
else:
    args += ["--onefile", "--hidden-import", "keyring.backends.SecretService", "--collect-submodules", "secretstorage",
             "--copy-metadata", "SecretStorage", "--copy-metadata", "jeepney", "--copy-metadata", "cryptography"]
args.append(str(root / "packaging/posix_entry.py"))
subprocess.run(args, cwd=root, check=True)
out = root / "dist"
out.mkdir(exist_ok=True)
if system == "macOS":
    app = dist / (name + ".app")
    exe = app / "Contents/MacOS" / name
    archive = out / f"{name}-{system}-{arch}.zip"
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)], check=True)
else:
    exe = dist / name
    archive = out / f"{name}-{system}-{arch}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(exe, arcname=name, recursive=False)
report = work / "self-test.json"
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "CODEX", "OPENAI", "OPENCODEX", "DEEPSEEK"))}
subprocess.run([str(exe), "--self-test", str(report)], check=True, timeout=60, env=env, cwd=work)
print(report.read_text())
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix(archive.suffix + ".sha256").write_text(digest + "  " + archive.name + "\n")
print("READY", archive.name, archive.stat().st_size, digest)
