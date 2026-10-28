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
from .catalog import deepseek_catalog, list_models, fetch, verify
from .config import atomic_write, candidate, commit
from .platforms import ensure_windows_bridge, find_linux_desktop, find_mac_desktop, find_posix_cli, is_desktop_running, posix_bridge, windows_runtime
from .probe import clean_env, validate
from .profiles import CredentialStore, Profile, data_dir


def has_personal_auth(config_home: Path) -> bool:
    """Detect an existing ChatGPT login without printing or copying credentials."""
    try:
        auth = json.loads((config_home / "auth.json").read_text(encoding="utf-8"))
        tokens = auth.get("tokens") or {}
        return auth.get("auth_mode") == "chatgpt" and bool(tokens.get("access_token"))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def launch(profile: Profile | None, personal_model: str, cli_path: str, desktop_path: str, isolate_catalog: bool = False):
    logger().info("launch requested mode=%s isolate_catalog=%s", profile.kind if profile else "personal", isolate_catalog)
    if is_desktop_running():
        raise RuntimeError("请先正常退出所有 Codex Desktop 窗口，然后再次启动；未修改配置。")
    if os.name == "nt":
        desktop, cli = windows_runtime()
        bridge = ensure_windows_bridge()
        command = [str(desktop)]
    else:
        desktop = None
        if sys.platform == "darwin":
            desktop = Path(desktop_path.strip()) if desktop_path.strip() else find_mac_desktop()
            if not desktop or not desktop.is_file():
                raise RuntimeError("找不到 Codex Desktop，请先将 Codex.app 或 ChatGPT.app 安装到应用程序目录。")
        elif sys.platform.startswith("linux"):
            desktop = find_linux_desktop()
        cli = Path(cli_path.strip()).resolve() if cli_path.strip() else find_posix_cli(desktop)
        if not cli or not cli.is_file():
            raise RuntimeError("找不到 Codex CLI。请先安装 Codex CLI，并确保 codex 位于 PATH 或 ~/.local/bin。")
        bridge = posix_bridge()
        if desktop:
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
        atomic_write(state_path, json.dumps({"model": str(root["model"]), "reasoning_present": "model_reasoning_effort" in root, "reasoning": str(root.get("model_reasoning_effort", ""))}).encode("utf-8"))
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
    if profile and profile.kind == "responses":
        candidates, native = list_models(profile, secret, cli)
        ids = {name for name, _ in candidates}
        selected = profile.model
        if selected == "last":
            try:
                selected = (data_dir() / "last-models" / (profile.id + ".txt")).read_text(encoding="utf-8").strip()
            except OSError:
                selected = "auto"
            if selected not in ids:
                selected = "auto"
        if selected == "auto":
            selected = next((name for name, item in candidates if item.get("is_default") or item.get("isDefault")), candidates[0][0])
        if selected not in ids:
            raise ValueError("所选中转站模型不可用，请在设置中测试连接并重新选择。")
        details = next(item for name, item in candidates if name == selected)
        levels = details.get("supported_reasoning_levels") or details.get("supportedReasoningEfforts") or []
        supported = {entry.get("effort") or entry.get("reasoningEffort") for entry in levels if isinstance(entry, dict)}
        if profile.reasoning_effort and supported and profile.reasoning_effort not in supported:
            raise ValueError("所选推理深度不受当前模型支持，请在设置中重新选择。")
        logger().info("relay model selected model=%s native_catalog=%s", selected, native)
        profile = dataclasses.replace(profile, model=selected, codex_catalog=native)
    adapter = None
    try:
        active = profile
        if profile and profile.kind == "deepseek":
            # Normalize saved legacy profiles without changing their credential IDs.
            profile = dataclasses.replace(profile, base_url="https://api.deepseek.com", model="deepseek-flash", codex_catalog=False)
            active = dataclasses.replace(profile, kind="responses")
            logger().info("DeepSeek native Responses API selected model=deepseek-flash")
        if profile and profile.kind == "openai_chat":
            adapter = ChatAdapter(profile, secret)
            adapter.__enter__()
            logger().info("local Chat adapter ready")
            active = dataclasses.replace(profile, kind="responses", base_url=adapter.url)
            secret = adapter.master
        catalog_path, catalog_models = None, None
        if profile and profile.kind == "deepseek":
            catalog_path, catalog_models = deepseek_catalog()
            logger().info("prepared DeepSeek model catalog for CLI validation")
        if profile and profile.codex_catalog:
            if profile.kind != "responses":
                raise ValueError("Codex 原生模型目录仅适用于 Responses 服务。")
            catalog_path, catalog_models = fetch(profile, secret, cli)
        personal_auth = bool(profile and has_personal_auth(config_home))
        proposed = candidate(original or b"", active, personal_model.strip() if profile is None else None, catalog_path, isolate_catalog, personal_auth, deepseek=bool(profile and profile.kind == "deepseek"))
        if profile is None and state_path.exists():
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            if "reasoning_present" in saved:
                personal_doc = tomlkit.parse(proposed.decode("utf-8"))
                if saved["reasoning_present"]:
                    personal_doc["model_reasoning_effort"] = saved["reasoning"]
                else:
                    personal_doc.pop("model_reasoning_effort", None)
                proposed = tomlkit.dumps(personal_doc).encode("utf-8")
        if profile:
            logger().info("personal account identity available=%s", personal_auth)
        env = clean_env()
        env["CODEX_SWITCH_REAL_CLI"] = str(cli)
        env["CODEX_SWITCH_PROVIDER"] = "launcher_custom" if profile else "openai"
        if profile and profile.kind == "responses":
            last_dir = data_dir() / "last-models"
            last_dir.mkdir(parents=True, exist_ok=True)
            env["CODEX_SWITCH_LAST_MODEL_FILE"] = str(last_dir / (profile.id + ".txt"))
        if profile and profile.kind == "deepseek":
            env["CODEX_SWITCH_FIXED_MODEL"] = profile.model
        env["CODEX_CLI_PATH"] = str(bridge)
        if secret:
            env["CODEX_LAUNCHER_API_KEY"] = secret
        def check(value):
            logger().info("validating candidate in isolated Codex home")
            result = validate(cli, value)
            if catalog_models is not None:
                verify(catalog_models, result)
                selected_model = next((item for item in result[3]["data"] if item.get("id") == active.model), {})
                tiers = selected_model.get("serviceTiers") or []
                logger().info("selected model Fast option available=%s", any(
                    isinstance(item, dict) and (item.get("id") in ("fast", "priority") or str(item.get("name", "")).lower() == "fast")
                    for item in tiers))
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
