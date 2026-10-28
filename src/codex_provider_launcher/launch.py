"""Launch orchestration: validate first, never touch chats or auth files."""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tomlkit

from .adapter import ChatAdapter
from .diagnostics import logger
from .catalog import discover, fetch, verify
from .config import atomic_write, candidate, commit
from .platforms import ensure_windows_bridge, is_desktop_running, posix_bridge, windows_runtime
from .probe import clean_env, validate
from .profiles import CredentialStore, Profile, data_dir


def launch(profile: Profile | None, personal_model: str, cli_path: str, desktop_path: str, isolate_catalog: bool = False):
    logger().info("launch requested mode=%s isolate_catalog=%s", profile.kind if profile else "personal", isolate_catalog)
    if is_desktop_running():
        raise RuntimeError("请先正常退出所有 Codex Desktop 窗口，然后再次启动；未修改配置。")
    if os.name == "nt":
        desktop, cli = windows_runtime()
        bridge = ensure_windows_bridge()
        command = [str(desktop)]
    else:
        cli_name = cli_path.strip() or shutil.which("codex")
        if not cli_name or not Path(cli_name).is_file():
            raise RuntimeError("找不到 Codex CLI，请在启动器中选择其路径。")
        cli = Path(cli_name).resolve()
        bridge = posix_bridge()
        if sys.platform == "darwin":
            desktop = Path(desktop_path.strip() or "/Applications/Codex.app/Contents/MacOS/Codex")
            if not desktop.is_file():
                raise RuntimeError("找不到 Codex Desktop，请在启动器中选择桌面程序的可执行文件。")
            command = [str(desktop)]
        else:
            terminal = shutil.which("x-terminal-emulator") or shutil.which("xterm")
            if not terminal:
                raise RuntimeError("未找到图形终端（x-terminal-emulator 或 xterm）。")
            command = [terminal, "-e", str(cli)]
    config_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    config_path = config_home / "config.toml"
    original = config_path.read_bytes() if config_path.exists() else None
    logger().info("Codex runtime resolved; reading configuration")
    root = tomlkit.parse((original or b"").decode("utf-8-sig"))
    state_path = data_dir() / "personal-model.json"
    if profile and root.get("model_provider", "openai") == "openai" and isinstance(root.get("model"), str):
        atomic_write(state_path, json.dumps({"model": str(root["model"])}).encode("utf-8"))
    if profile is None and not personal_model.strip() and state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        personal_model = state.get("model", "") if isinstance(state.get("model", ""), str) else ""
    if profile is None and not personal_model.strip() and root.get("model_provider", "openai") != "openai":
        models = validate(cli, b"")[3]["data"]
        available = [m for m in models if not m.get("hidden") and m.get("id")]
        default = next((m for m in available if m.get("isDefault")), available[0] if available else None)
        if not default:
            raise RuntimeError("无法确定个人账号模型，配置未修改。")
        personal_model = default["id"]
    secret = CredentialStore().get(profile.id) if profile else None
    if profile and not secret:
        raise RuntimeError("未找到服务密钥，请先保存到系统凭据库。")
    if profile and profile.kind == "responses" and profile.model == "auto":
        preferred = str(root.get("model", ""))
        selected, native = discover(profile, secret, cli, preferred)
        logger().info("relay model selected model=%s native_catalog=%s", selected, native)
        profile = dataclasses.replace(profile, model=selected, codex_catalog=native)
    adapter = None
    try:
        active = profile
        if profile and profile.kind in ("deepseek", "openai_chat"):
            adapter = ChatAdapter(profile, secret)
            adapter.__enter__()
            logger().info("local Chat adapter ready")
            active = dataclasses.replace(profile, kind="responses", base_url=adapter.url)
            secret = adapter.master
        catalog_path, catalog_models = None, None
        if profile and profile.codex_catalog:
            if profile.kind != "responses":
                raise ValueError("Codex 原生模型目录仅适用于 Responses 服务。")
            catalog_path, catalog_models = fetch(profile, secret, cli)
        proposed = candidate(original or b"", active, personal_model.strip() if profile is None else None, catalog_path, isolate_catalog)
        env = clean_env()
        env["CODEX_SWITCH_REAL_CLI"] = str(cli)
        env["CODEX_SWITCH_PROVIDER"] = "launcher_custom" if profile else "openai"
        env["CODEX_CLI_PATH"] = str(bridge)
        if secret:
            env["CODEX_LAUNCHER_API_KEY"] = secret
        def check(value):
            logger().info("validating candidate in isolated Codex home")
            result = validate(cli, value)
            if catalog_models is not None:
                verify(catalog_models, result)
        commit(config_path, original, proposed, data_dir() / "backups", check)
        logger().info("configuration validated and saved; recovery record created")
        try:
            child = subprocess.Popen(command, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            logger().info("Codex launch process created pid=%s", getattr(child, "pid", "unknown"))
        except BaseException:
            # Restore only if nobody changed the candidate after commit.
            if config_path.exists() and config_path.read_bytes() == proposed:
                if original is None:
                    config_path.unlink()
                else:
                    atomic_write(config_path, original)
            raise
        return child, adapter
    except BaseException:
        if adapter:
            adapter.__exit__(None, None, None)
        raise
