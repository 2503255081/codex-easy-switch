"""Conservative candidate generation and transactional configuration writes."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import tomlkit
from filelock import FileLock

from .profiles import Profile, data_dir

PROVIDER = "launcher_custom"
CONFLICTS = ("profile", "model_catalog_json", "openai_base_url", "forced_login_method")


def candidate(original: bytes, profile: Profile | None, personal_model: str | None = None, catalog: Path | None = None, isolate_catalog: bool = False, personal_auth: bool = False, deepseek: bool = False) -> bytes:
    """Generate a candidate. Unrecognized overrides are kept and stop switching."""
    doc = tomlkit.parse(original.decode("utf-8-sig"))
    if "model_catalog_json" in doc:
        old = Path(str(doc["model_catalog_json"])).expanduser().resolve()
        root = (data_dir() / "catalogs").resolve()
        if isolate_catalog or (old.parent == root and old.name.startswith("catalog-") and old.suffix == ".json"):
            del doc["model_catalog_json"]
    conflicts = [key for key in CONFLICTS if key in doc]
    if conflicts:
        raise ValueError("检测到需要人工检查的旧配置：" + ", ".join(conflicts))
    providers = doc.get("model_providers", {})
    if not hasattr(providers, "get"):
        raise ValueError("model_providers 格式不受支持。")
    if "openai" in providers:
        raise ValueError("检测到自定义 openai 提供方，请先人工检查，未修改配置。")
    existing = providers.get(PROVIDER)
    if existing is not None and (not hasattr(existing, "get") or existing.get("env_key") != "CODEX_LAUNCHER_API_KEY"):
        raise ValueError("检测到名称冲突的第三方提供方，请先人工检查，未修改配置。")
    if profile is None:
        if doc.get("model_provider") == PROVIDER and not personal_model:
            raise ValueError("请填写个人账号使用的模型 ID，避免把中转模型用于个人账号。")
        doc["model_provider"] = "openai"
        if personal_model:
            if any(c.isspace() for c in personal_model) or len(personal_model) > 200:
                raise ValueError("个人账号模型 ID 无效。")
            doc["model"] = personal_model
    else:
        profile.validate()
        if profile.kind != "responses":
            raise ValueError("此配置必须先启动协议适配器。")
        doc["model_provider"] = PROVIDER
        doc["model"] = profile.model
        if "model_providers" not in doc:
            doc["model_providers"] = tomlkit.table()
        entry = tomlkit.table()
        entry["name"] = profile.name
        entry["base_url"] = profile.base_url
        entry["wire_api"] = "responses"
        entry["env_key"] = "CODEX_LAUNCHER_API_KEY"
        if personal_auth:
            entry["requires_openai_auth"] = True
        doc["model_providers"][PROVIDER] = entry
        if catalog is not None:
            doc["model_catalog_json"] = str(catalog)
    if deepseek:
        # Desktop filters the model catalog against this separate effort allowlist.
        desktop = doc.setdefault("desktop", tomlkit.table())
        efforts = desktop.get("enabled-reasoning-efforts", ["low", "medium", "high", "xhigh", "ultra", "max"])
        if not isinstance(efforts, list) or any(not isinstance(e, str) for e in efforts):
            raise ValueError("desktop.enabled-reasoning-efforts 格式不受支持。")
        desktop["enabled-reasoning-efforts"] = list(dict.fromkeys([*efforts, "none", "low", "high", "max"]))
    return tomlkit.dumps(doc).encode("utf-8")


def atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".launcher-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def commit(path: Path, expected: bytes | None, updated: bytes, backups: Path, validate) -> Path | None:
    """Validate before acquiring lock; prevent overwrite after concurrent edit."""
    validate(updated)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.parent / ".codex-launcher.lock"), timeout=5):
        current = path.read_bytes() if path.exists() else None
        if current != expected:
            raise RuntimeError("验证期间配置发生变化，请重试；未覆盖用户修改。")
        backup = None
        if current is not None:
            backups.mkdir(parents=True, exist_ok=True, mode=0o700)
            digest = hashlib.sha256(current).hexdigest()
            backup = backups / (digest + ".toml.bak")
            if not backup.exists():
                atomic_write(backup, current)
        record = {"config_path": str(path.resolve()), "before": hashlib.sha256(current).hexdigest() if current is not None else None,
                  "after": hashlib.sha256(updated).hexdigest()}
        atomic_write(backups / "last-change.json", json.dumps(record).encode("utf-8"))
        atomic_write(path, updated)
        return backup


def restore_last(path: Path, backups: Path) -> None:
    """Restore the exact previous bytes only if the current file was not edited."""
    with FileLock(str(path.parent / ".codex-launcher.lock"), timeout=5):
        record_path = backups / "last-change.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("config_path") != str(path.resolve()) or not path.is_file():
            raise ValueError("恢复记录与当前配置不匹配。")
        current = path.read_bytes()
        if hashlib.sha256(current).hexdigest() != record.get("after"):
            raise ValueError("配置在切换后已有新修改，已停止恢复以免覆盖。")
        before = record.get("before")
        if before is not None and (not isinstance(before, str) or len(before) != 64 or any(c not in "0123456789abcdef" for c in before)):
            raise ValueError("恢复记录无效。")
        original = (backups / (before + ".toml.bak")).read_bytes() if before else None
        if original is not None and hashlib.sha256(original).hexdigest() != before:
            raise ValueError("备份内容校验失败。")
        atomic_write(backups / (hashlib.sha256(current).hexdigest() + ".toml.bak"), current)
        if original is None:
            path.unlink()
        else:
            atomic_write(path, original)
        record_path.unlink()
