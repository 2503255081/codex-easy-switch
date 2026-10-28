"""Platform discovery; never close an existing Codex process."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .profiles import data_dir


def system_powershell() -> str:
    """Use native PowerShell even from the x86 launcher on x64 Windows."""
    windows = Path(os.environ.get("WINDIR", "C:/Windows"))
    native = windows / "Sysnative" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    standard = windows / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(native if native.is_file() else standard)


def windows_runtime() -> tuple[Path, Path]:
    if os.name != "nt":
        raise RuntimeError("此操作只适用于 Windows。")
    query = "(Get-AppxPackage -Name OpenAI.Codex | Sort-Object Version -Descending | Select-Object -First 1).InstallLocation"
    result = subprocess.run([system_powershell(), "-NoProfile", "-NonInteractive", "-Command", query],
                            capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("找不到 Codex Desktop 安装包。")
    package = Path(result.stdout.strip())
    app = package / "app" / "ChatGPT.exe"
    bundled = package / "app" / "resources" / "codex.exe"
    if not app.is_file() or not bundled.is_file():
        raise RuntimeError("Codex Desktop 安装目录不完整。")
    digest = hashlib.sha256(bundled.read_bytes()).digest()
    root = Path(os.environ["LOCALAPPDATA"]) / "OpenAI" / "Codex" / "bin"
    for cli in root.glob("*/codex.exe"):
        if hashlib.sha256(cli.read_bytes()).digest() == digest:
            return app, cli
    raise RuntimeError("未找到与桌面版匹配的 Codex CLI；请先正常启动一次官方 Codex。")


def ensure_windows_bridge() -> Path:
    if os.name != "nt":
        raise RuntimeError("Windows bridge is unavailable on this system")
    bundled = Path(__file__).resolve().parent / "ProviderBridge.exe"
    if getattr(sys, "frozen", False) and bundled.is_file():
        # Persist outside the one-file extraction folder: Codex outlives the launcher.
        from .config import atomic_write
        raw = bundled.read_bytes()
        directory = data_dir() / "runtime"
        result = directory / ("bridge-" + hashlib.sha256(raw).hexdigest()[:16] + ".exe")
        if not result.exists() or result.read_bytes() != raw:
            atomic_write(result, raw)
        return result
    source = Path(__file__).resolve().parent / "ProviderBridge.cs"
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not source.is_file() or not compiler.is_file():
        raise RuntimeError("缺少桥接源码或 .NET 编译器。")
    directory = data_dir() / "runtime"
    directory.mkdir(parents=True, exist_ok=True)
    result = directory / ("bridge-" + hashlib.sha256(source.read_bytes()).hexdigest()[:16] + ".exe")
    if not result.exists():
        proc = subprocess.run([str(compiler), "/nologo", "/target:exe", "/reference:System.Web.Extensions.dll",
                               "/out:" + str(result), str(source)], capture_output=True,
                              timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        if proc.returncode or not result.exists():
            raise RuntimeError("无法编译桌面端路由桥接程序。")
    return result


def posix_bridge() -> Path:
    directory = data_dir() / "runtime"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "provider-bridge"
    # Executable shebang permits a path only; quoting in shell handles spaces in Python path.
    import shlex
    script = "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -m codex_provider_launcher.bridge \"$@\"\n"
    path.write_text(script, encoding="utf-8")
    path.chmod(0o700)
    return path


def is_desktop_running() -> bool:
    if os.name == "nt":
        query = "Get-CimInstance Win32_Process -Filter \"Name='ChatGPT.exe'\" | ForEach-Object { $_.ExecutablePath }"
        result = subprocess.run([system_powershell(), "-NoProfile", "-NonInteractive", "-Command", query],
                                capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError("无法确认 Codex 是否已经运行；为保护配置已停止。")
        return any("openai.codex_" in line.lower() for line in result.stdout.splitlines())
    if sys.platform == "darwin":
        result = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=10, check=True)
        return any("Codex.app/Contents/MacOS/" in line for line in result.stdout.splitlines())
    return False
