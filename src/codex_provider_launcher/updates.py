"""Check public GitHub releases without accessing provider profiles or secrets."""
from __future__ import annotations

from dataclasses import dataclass
import json
import platform
import re
import struct
import sys
import time
import urllib.request

from . import __version__
from .config import atomic_write
from .network import build_api_opener
from .profiles import data_dir

REPOSITORY = "2503255081/codex-easy-switch"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
INTERVAL = 24 * 60 * 60


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"v?\d{1,6}\.\d{1,6}\.\d{1,6}", value):
        raise ValueError("版本信息格式无效。")
    return tuple(map(int, value.removeprefix("v").split(".")))


def asset_name() -> str | None:
    prefix = "Codex-Provider-Launcher-"
    if sys.platform == "win32":
        return prefix + ("Windows-x64.exe" if struct.calcsize("P") == 8 else "Windows-x86.exe")
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if arch and sys.platform in ("darwin", "linux"):
        return prefix + (f"macOS-{arch}.zip" if sys.platform == "darwin" else f"Linux-{arch}.tar.gz")
    return None


@dataclass(frozen=True)
class Update:
    version: str
    release_url: str
    download_url: str


def available_update(document: dict) -> Update | None:
    if not isinstance(document, dict):
        raise ValueError("更新信息格式无效。")
    if document.get("draft") or document.get("prerelease"):
        return None
    tag = document.get("tag_name", "")
    if version_tuple(tag) <= version_tuple(__version__):
        return None
    root = f"https://github.com/{REPOSITORY}/releases"
    release_url = f"{root}/tag/{tag}"
    name = asset_name()
    expected = f"{root}/download/{tag}/{name}"
    assets = document.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("更新文件列表格式无效。")
    found = name and any(isinstance(a, dict) and a.get("name") == name
                        and a.get("browser_download_url") == expected and a.get("state") == "uploaded" for a in assets)
    return Update(tag.removeprefix("v"), release_url, expected if found else release_url)


def check_update(force: bool = False) -> Update | None:
    path = data_dir() / "update-check.json"
    now = time.time()
    if not force:
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
            if 0 <= now - cache["checked_at"] < INTERVAL:
                return available_update(cache["release"])
        except (OSError, ValueError, TypeError, KeyError):
            pass
    request = urllib.request.Request(RELEASE_API, headers={"Accept": "application/vnd.github+json",
                                      "User-Agent": "Codex-Easy-Switch/" + __version__})
    with build_api_opener().open(request, timeout=10) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("更新信息过大。")
    document = json.loads(raw)
    update = available_update(document)
    # Save only public release fields. A failed network check is not cached.
    release = {k: document.get(k) for k in ("tag_name", "draft", "prerelease", "assets")}
    try:
        atomic_write(path, json.dumps({"checked_at": now, "release": release}).encode("utf-8"))
    except OSError:
        pass
    return update
