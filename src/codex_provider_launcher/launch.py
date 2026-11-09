"""Validate configuration, migrate backed-up route metadata, and launch Desktop."""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tomlkit
from filelock import FileLock

from .adapter import ChatAdapter
from .diagnostics import logger
from .catalog import deepseek_catalog, fixed_catalog, list_models, fetch, verify, reasoning_levels
from .config import atomic_write, candidate, commit
from .platforms import ensure_windows_bridge, find_linux_desktop, find_mac_desktop, find_posix_cli, is_desktop_running, posix_bridge, windows_runtime
from .probe import clean_env, validate
from .profiles import CredentialStore, Profile, data_dir
from .history import migrate_routes, restore_routes
from .desktop_state import prepare_view


def default_model(candidates: list[tuple[str, dict]]) -> str:
    """Do not auto-select an embeddings/moderation/audio model for a chat."""
    import re
    available = [(name, item) for name, item in candidates if not re.search(
        r"moderation|embedding|whisper|(^|[-_/])(tts|dall-e|sora|audio|realtime|transcribe|image)([-_/]|$)", name, re.I)]
    if not available:
        raise ValueError("未找到可用于对话的模型，请在设置中手动填写模型名称。")
    return next((name for name, item in available if item.get("is_default") or item.get("isDefault")), available[0][0])


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
            selected = default_model(candidates)
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
            model = profile.model if profile.model in ("deepseek-flash", "deepseek-v4-pro") else "deepseek-flash"
            profile = dataclasses.replace(profile, base_url="https://api.deepseek.com", model=model, codex_catalog=False)
            active = dataclasses.replace(profile, kind="responses")
            logger().info("DeepSeek native Responses API selected model=%s", model)
        if profile and profile.kind == "fixed_responses":
            levels = reasoning_levels(profile.provider, profile.model)
            effort = profile.reasoning_effort or ("low" if "low" in levels else "high")
            if effort not in levels:
                raise ValueError("所选推理深度不受当前模型支持，请在设置中重新选择。")
            active = dataclasses.replace(profile, kind="responses", reasoning_effort=effort, models=[])
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
        if profile and profile.kind == "fixed_responses":
            catalog_path, catalog_models = fixed_catalog(profile.model, profile.name, profile.models, profile.provider)
        if profile and profile.codex_catalog:
            if profile.kind != "responses":
                raise ValueError("Codex 原生模型目录仅适用于 Responses 服务。")
            catalog_path, catalog_models = fetch(profile, secret, cli)
        personal_auth = False  # API authentication must remain independent of ChatGPT.
        extras = tuple(dict.fromkeys(e for item in (catalog_models or []) for level in item.get("supported_reasoning_levels", []) if isinstance(level, dict) for e in [level.get("effort")] if isinstance(e, str))) if profile and profile.kind == "fixed_responses" else ()
        proposed = candidate(original or b"", active, personal_model.strip() if profile is None else None, catalog_path, isolate_catalog, personal_auth, deepseek=bool(profile and profile.kind == "deepseek"), extra_efforts=extras)
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
            logger().info("OpenAI authentication enabled for provider=%s quota_compatibility=%s", personal_auth, profile.quota_compatibility)
        env = clean_env()
        env["CODEX_SWITCH_REAL_CLI"] = str(cli)
        env["CODEX_SWITCH_PROVIDER"] = "launcher_custom" if profile else "openai"
        if profile and profile.kind == "responses":
            last_dir = data_dir() / "last-models"
            last_dir.mkdir(parents=True, exist_ok=True)
            env["CODEX_SWITCH_LAST_MODEL_FILE"] = str(last_dir / (profile.id + ".txt"))
        if profile and profile.kind in ("deepseek", "fixed_responses"):
            env["CODEX_SWITCH_FIXED_MODEL"] = profile.model
            if profile.kind == "deepseek":
                env["CODEX_SWITCH_ALLOWED_MODELS"] = json.dumps(["deepseek-flash", "deepseek-v4-pro"])
            elif profile.models:
                env["CODEX_SWITCH_ALLOWED_MODELS"] = json.dumps(profile.models, ensure_ascii=True)
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
        # Serialize config/index writes and process creation across launcher instances.
        with FileLock(str(config_home / ".codex-launcher-start.lock"), timeout=5):
            if is_desktop_running():
                raise RuntimeError("Codex Desktop 已启动，请退出后重试；未修改配置。")
            commit(config_path, original, proposed, data_dir() / "backups", check)
            logger().info("configuration validated and saved; recovery record created")
            route_backup = None
            view_change = None
            try:
                if is_desktop_running():
                    raise RuntimeError("Codex Desktop 已启动，请退出后重试。")
                route_backup = migrate_routes(config_home, "launcher_custom" if profile else "openai", data_dir() / "backups")
                if route_backup:
                    record_path = data_dir() / "backups" / "last-change.json"
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    record["history_routes"] = route_backup.name
                    atomic_write(record_path, json.dumps(record).encode("utf-8"))
                    logger().info("paginated chat provider metadata migrated; recovery=%s", route_backup.name)
                view_change = prepare_view(config_home, api=bool(profile), backups=data_dir() / "backups")
                if view_change:
                    logger().info("local desktop project view prepared mode=%s", "api" if profile else "personal")
                child = subprocess.Popen(command, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                logger().info("Codex launch process created pid=%s", getattr(child, "pid", "unknown"))
            except BaseException:
                try:
                    try:
                        if view_change:
                            view_change.rollback()
                    finally:
                        if route_backup:
                            restore_routes(config_home, route_backup)
                finally:
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
