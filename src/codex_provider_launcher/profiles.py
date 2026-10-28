from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import keyring


SERVICE = "codex-provider-launcher"
KINDS = ("responses", "openai_chat", "deepseek")


def data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / SERVICE
    if os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / SERVICE
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / SERVICE


def normalize_url(raw: str, allow_http: bool = False) -> str:
    value = raw.strip().rstrip("/")
    parsed = urlsplit(value)
    if any(c.isspace() or ord(c) < 32 for c in value) or parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("请输入不含用户名、密码或查询参数的 HTTP(S) API 地址。")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1") and not allow_http:
        raise ValueError("非本机 HTTP 地址会明文传输密钥；请使用 HTTPS 或显式允许 HTTP。")
    return value


@dataclass
class Profile:
    id: str
    name: str
    kind: str
    base_url: str
    model: str
    allow_http: bool = False
    codex_catalog: bool = False

    def validate(self) -> None:
        uuid.UUID(self.id)
        if not self.name.strip() or self.kind not in KINDS:
            raise ValueError("请输入名称并选择有效的接口类型。")
        self.base_url = normalize_url(self.base_url, self.allow_http)
        if not self.model.strip() or any(c.isspace() for c in self.model) or len(self.model) > 200:
            raise ValueError("请输入有效的模型 ID。")
        self.model = self.model.strip()


class ProfileStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or data_dir()
        self.path = self.directory / "profiles.json"

    def load(self) -> list[Profile]:
        if not self.path.exists():
            return []
        document = json.loads(self.path.read_text(encoding="utf-8"))
        if document.get("version") != 1 or not isinstance(document.get("profiles"), list):
            raise ValueError("启动器配置格式不受支持。")
        result = [Profile(**entry) for entry in document["profiles"]]
        for profile in result:
            profile.validate()
        if len({p.id for p in result}) != len(result):
            raise ValueError("启动器配置含有重复的服务 ID。")
        return result

    def save(self, profiles: list[Profile]) -> None:
        for profile in profiles:
            profile.validate()
        if len({p.id for p in profiles}) != len(profiles):
            raise ValueError("启动器配置含有重复的服务 ID。")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw = json.dumps({"version": 1, "profiles": [asdict(p) for p in profiles]}, ensure_ascii=False, indent=2) + "\n"
        fd, path = tempfile.mkstemp(prefix=".profiles-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, self.path)
            if os.name != "nt":
                os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class CredentialStore:
    def _check(self) -> None:
        backend = keyring.get_keyring()
        if backend.priority <= 0 or "plaintext" in type(backend).__module__.lower():
            raise RuntimeError("没有可用的系统凭据库。请启用 Windows 凭据管理器、macOS 钥匙串或 Linux Secret Service。")

    def get(self, profile_id: str) -> str | None:
        self._check()
        secret = keyring.get_password(SERVICE, profile_id)
        from .diagnostics import protect
        protect(secret)
        return secret

    def put(self, profile_id: str, secret: str) -> None:
        from .diagnostics import protect
        protect(secret)
        self._check()
        if not secret or any(c.isspace() for c in secret):
            raise ValueError("密钥不能为空或包含空白字符。")
        keyring.set_password(SERVICE, profile_id, secret)

    def delete(self, profile_id: str) -> None:
        self._check()
        if self.get(profile_id) is not None:
            keyring.delete_password(SERVICE, profile_id)


def new_profile(name: str, kind: str, url: str, model: str, allow_http: bool = False, codex_catalog: bool = False) -> Profile:
    profile = Profile(str(uuid.uuid4()), name.strip(), kind, url, model, allow_http, codex_catalog)
    profile.validate()
    return profile
