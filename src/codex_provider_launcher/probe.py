"""Run Codex app-server validation against an isolated temporary home."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

_OVERRIDES = ("OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_CLI_PATH", "CODEX_LAUNCHER_API_KEY", "CODEX_PROFILE", "CODEX_MODEL_PROVIDER")


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper() not in _OVERRIDES and not k.upper().startswith("CODEX_SWITCH_")}


def validate(cli: Path, config: bytes, timeout: float = 25) -> dict:
    with tempfile.TemporaryDirectory(prefix="codex-launcher-probe-") as d:
        root = Path(d)
        (root / "config.toml").write_bytes(config)
        env = clean_env()
        env["CODEX_HOME"] = str(root)
        env["CODEX_LAUNCHER_API_KEY"] = "validation-placeholder"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen([str(cli), "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                   env=env, creationflags=flags)
        messages: queue.Queue = queue.Queue()
        def reader():
            try:
                for line in process.stdout:
                    messages.put(line)
            finally:
                messages.put(None)
        threading.Thread(target=reader, daemon=True).start()
        def send(obj):
            process.stdin.write(json.dumps(obj, separators=(",", ":")) + "\n")
            process.stdin.flush()
        try:
            send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "launcher-validation", "version": "1"}, "capabilities": {"experimentalApi": True}}})
            results = {}
            deadline = time.monotonic() + timeout
            while len(results) < 2:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Codex 配置校验超时。")
                try:
                    line = messages.get(timeout=remaining)
                except queue.Empty as e:
                    raise TimeoutError("Codex 配置校验超时。") from e
                if line is None:
                    raise RuntimeError("Codex 在配置校验期间退出。")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("method") == "configWarning" and "Invalid configuration" in str(msg.get("params", {}).get("summary", "")):
                    raise ValueError("Codex 拒绝此配置，请检查旧插件或模型目录。")
                if msg.get("error"):
                    raise ValueError("Codex 配置校验失败：" + str(msg["error"].get("message", "未知错误")))
                if msg.get("id") == 1:
                    send({"method": "initialized"})
                    send({"id": 2, "method": "config/read", "params": {"includeLayers": False}})
                    send({"id": 3, "method": "model/list", "params": {"includeHidden": True, "limit": 100}})
                elif msg.get("id") in (2, 3):
                    results[msg["id"]] = msg.get("result", {})
            if not results[3].get("data") or results[3].get("nextCursor"):
                raise ValueError("Codex 模型列表为空或未能完整读取。")
            return results
        finally:
            process.kill()
            process.communicate(timeout=5)
