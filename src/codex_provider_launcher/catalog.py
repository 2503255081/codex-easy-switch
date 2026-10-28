"""Validate optional OpenCodex native catalog before attaching it to Codex."""
from __future__ import annotations

import hashlib
import copy
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


def deepseek_catalog() -> tuple[Path, list[dict]]:
    """Build from the installed Codex cache; the caller validates with its CLI."""
    import os
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    try:
        cached = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
        models = cached.get("models")
        template = next(m for m in models if isinstance(m, dict) and m.get("visibility") == "list" and m.get("slug"))
    except (OSError, ValueError, TypeError, StopIteration) as exc:
        raise RuntimeError("找不到可用的 Codex 原生模型目录。请先用个人账号正常启动一次 Codex 后重试。") from exc
    model = copy.deepcopy(template)
    model.update(slug="deepseek-chat", display_name="DeepSeek Chat", description="DeepSeek 官方 API（通过本地适配器）",
                 visibility="list", priority=0, default_reasoning_level="low", context_window=64000,
                 max_context_window=64000, input_modalities=["text"], additional_speed_tiers=[],
                 service_tiers=[], availability_nux=None, upgrade=None)
    model["supported_reasoning_levels"] = [{"effort": "low", "description": "默认"}]
    models = [model]
    raw = json.dumps({"models": models}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path = data_dir() / "catalogs" / ("catalog-" + hashlib.sha256(raw).hexdigest() + ".json")
    if path.exists() and path.read_bytes() != raw:
        raise RuntimeError("模型目录文件不匹配。")
    if not path.exists():
        atomic_write(path, raw)
    return path, models


def discover(profile: Profile, key: str, cli: Path, preferred: str = "") -> tuple[str, bool]:
    """Find an enabled model without asking the user for a model ID."""
    version = subprocess.run([str(cli), "--version"], capture_output=True, text=True, timeout=10,
                             env=clean_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    match = re.match(r"^codex-cli\s+(\S+)", version.stdout.strip())
    if version.returncode or not match:
        raise RuntimeError("无法读取 Codex 版本，未更改配置。")
    url = profile.base_url + "/models?" + urllib.parse.urlencode({"client_version": match.group(1)})
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key,
        "x-opencodex-api-key": key, "Accept": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("模型列表过大。")
        document = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise RuntimeError("无法读取中转站的模型列表，请检查地址、密钥和服务是否支持 /models。配置未修改。") from exc
    if not isinstance(document, dict):
        raise ValueError("中转站返回的模型列表格式不受支持。")
    native = isinstance(document.get("models"), list) and bool(document["models"])
    items = document["models"] if native else document.get("data")
    if not isinstance(items, list) or len(items) > 1000:
        raise ValueError("中转站没有返回可用的模型列表，配置未修改。")
    candidates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("slug") if native else item.get("id")
        visible = item.get("visibility") == "list" if native else not item.get("hidden", False)
        if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", name) and visible:
            candidates.append((name, item))
    ids = {name for name, _ in candidates}
    candidates = [(name, item) for name, item in candidates if not (name.endswith("--fast") and name[:-6] in ids)]
    if not candidates:
        raise ValueError("中转站没有可用的可见模型，配置未修改。")
    if preferred and any(name == preferred for name, _ in candidates):
        return preferred, native
    for name, item in candidates:
        if item.get("is_default") is True or item.get("isDefault") is True:
            return name, native
    return candidates[0][0], native


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
    by_id = {item["id"]: item for item in data}
    for model in models:
        if model["visibility"] != "list":
            continue
        advertised = "fast" in (model.get("additional_speed_tiers") or []) or any(
            isinstance(t, dict) and (t.get("id") in ("fast", "priority") or str(t.get("name", "")).lower() == "fast")
            for t in (model.get("service_tiers") or []))
        entry = by_id[model["slug"]]
        selectable = any(isinstance(t, dict) and (t.get("id") in ("fast", "priority") or str(t.get("name", "")).lower() == "fast")
                         for t in (entry.get("serviceTiers") or []))
        if advertised and not selectable:
            raise ValueError("中转站声明了 Fast，但当前 Codex 未识别为可选择的速度档位：" + model["slug"])
