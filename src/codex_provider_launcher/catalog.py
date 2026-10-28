"""Validate optional OpenCodex native catalog before attaching it to Codex."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from .config import atomic_write
from .probe import clean_env
from .profiles import Profile, data_dir


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(profile: Profile, key: str, cli: Path) -> tuple[Path, list[dict]]:
    version = subprocess.run([str(cli), "--version"], capture_output=True, text=True, timeout=10,
                             env=clean_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    match = re.match(r"^codex-cli\s+(\S+)", version.stdout.strip())
    if version.returncode or not match:
        raise RuntimeError("无法读取当前 Codex CLI 版本，模型列表未更改。")
    url = profile.base_url + "/models?" + urllib.parse.urlencode({"client_version": match.group(1)})
    req = urllib.request.Request(url, headers={"x-opencodex-api-key": key, "Accept": "application/json"})
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("服务返回的模型目录过大。")
    models = json.loads(raw).get("models")
    if not isinstance(models, list) or not 0 < len(models) <= 1000:
        raise ValueError("服务没有返回有效的 Codex 模型目录。")
    entries = {}
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("slug"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", model["slug"]):
            raise ValueError("模型目录中存在无效的模型 ID。")
        if model.get("visibility") not in ("list", "hide") or model["slug"] in entries:
            raise ValueError("模型目录包含无效的可见性或重复模型。")
        entries[model["slug"]] = model
    models = [m for m in models if not (m["slug"].endswith("--fast") and m["slug"][:-6] in entries and entries[m["slug"][:-6]]["visibility"] == "list")]
    if profile.model not in [m["slug"] for m in models if m["visibility"] == "list"]:
        raise ValueError("所选模型未在服务的可见模型目录中启用。")
    raw = json.dumps({"models": models}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path = data_dir() / "catalogs" / ("catalog-" + hashlib.sha256(raw).hexdigest() + ".json")
    if path.exists() and path.read_bytes() != raw:
        raise RuntimeError("模型目录文件不匹配。")
    if not path.exists():
        atomic_write(path, raw)
    return path, models


def verify(models: list[dict], results: dict) -> None:
    data = results[3]["data"]
    actual = {item["id"]: not item.get("hidden", False) for item in data}
    expected = {model["slug"]: model["visibility"] == "list" for model in models}
    if actual != expected:
        raise ValueError("Codex 读取的模型列表与服务端目录不一致，配置未更改。")
