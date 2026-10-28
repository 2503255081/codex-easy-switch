"""Local authenticated LiteLLM adapter for Chat Completions providers."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .probe import clean_env
from .diagnostics import protect
from .profiles import Profile


class ChatAdapter:
    def __init__(self, profile: Profile, secret: str, executable: str | None = None):
        profile.validate()
        if profile.kind not in ("deepseek", "openai_chat"):
            raise ValueError("该服务不是 Chat Completions 类型。")
        if not secret or any(c.isspace() for c in secret):
            raise ValueError("密钥无效。")
        self.profile, self.secret = profile, secret
        beside_python = Path(sys.executable).parent / ("litellm.exe" if os.name == "nt" else "litellm")
        self.executable = executable or (str(beside_python) if beside_python.is_file() else shutil.which("litellm"))
        self.process = None
        self.temp = None
        self.master = "sk-" + secrets.token_hex(32)
        protect(secret)
        protect(self.master)
        self.url = None

    def __enter__(self):
        if not self.executable:
            raise RuntimeError("未安装 LiteLLM 适配器，请安装 chat 可选依赖。")
        self.temp = tempfile.TemporaryDirectory(prefix="codex-launcher-adapter-")
        config = {"model_list": [{"model_name": self.profile.model,
                  "litellm_params": {"model": "openai/chat_completions/" + self.profile.model,
                                     "api_base": self.profile.base_url,
                                     "api_key": "os.environ/LAUNCHER_UPSTREAM_KEY"}}],
                  "general_settings": {"master_key": "os.environ/LAUNCHER_PROXY_KEY"}}
        config_path = Path(self.temp.name) / "adapter.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        if os.name != "nt":
            config_path.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/v1"
        env = clean_env()
        env["LAUNCHER_UPSTREAM_KEY"] = self.secret
        env["LAUNCHER_PROXY_KEY"] = self.master
        env["LITELLM_TELEMETRY"] = "False"
        try:
            self.process = subprocess.Popen([self.executable, "--host", "127.0.0.1", "--port", str(port), "--config", str(config_path)],
                                            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("LiteLLM 适配器启动失败。")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/liveliness", timeout=1) as response:
                        if response.status == 200:
                            return self
                except (OSError, urllib.error.URLError):
                    time.sleep(0.2)
            raise TimeoutError("LiteLLM 适配器启动超时。")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.temp is not None:
            self.temp.cleanup()
