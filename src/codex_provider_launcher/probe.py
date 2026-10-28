"""Run Codex app-server validation against an isolated temporary home."""
from __future__ import annotations

import json
import signal
import sys
import os
import queue
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .diagnostics import logger

_OVERRIDES = ("OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_CLI_PATH", "CODEX_LAUNCHER_API_KEY", "CODEX_PROFILE", "CODEX_MODEL_PROVIDER")


@contextmanager
def probe_home():
    directory = tempfile.TemporaryDirectory(prefix="codex-launcher-probe-", ignore_cleanup_errors=True)
    try:
        yield Path(directory.name)
    finally:
        directory.cleanup()
        if Path(directory.name).exists():
            logger().warning("Temporary probe directory is still in use: %s", directory.name)


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper() not in _OVERRIDES and not k.upper().startswith("CODEX_SWITCH_")}



class ProbeJob:
    """Keep the short-lived validation process and its children isolated."""
    def __init__(self, process):
        self.process = process
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [("a", ctypes.c_longlong), ("b", ctypes.c_longlong), ("flags", wintypes.DWORD),
                        ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                        ("f", ctypes.c_size_t), ("g", wintypes.DWORD), ("h", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(x, ctypes.c_ulonglong) for x in "abcdef"]
        class Limits(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO)] + [(x, ctypes.c_size_t) for x in "abcd"]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        job = kernel.CreateJobObjectW(None, None)
        limits = Limits()
        limits.basic.flags = 0x2000  # Kill job children when the last job handle closes.
        if not job:
            logger().warning("Cannot create probe job: %s", ctypes.get_last_error())
        elif not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, int(process._handle)):
            logger().warning("Cannot assign probe job: %s", ctypes.get_last_error())
            kernel.CloseHandle(job)
        else:
            self.handle, self.kernel = job, kernel

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        elif os.name != "nt":
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif self.process.poll() is None:
            # Fallback only targets the validation process tree we just started.
            tool = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32" / "taskkill.exe"
            subprocess.run([str(tool), "/PID", str(self.process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def validate(cli: Path, config: bytes, timeout: float = 25) -> dict:
    with probe_home() as root:
        (root / "config.toml").write_bytes(config)
        env = clean_env()
        env["CODEX_HOME"] = str(root)
        env["CODEX_LAUNCHER_API_KEY"] = "validation-placeholder"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen([str(cli), "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                   env=env, creationflags=flags, start_new_session=(os.name != "nt"))
        job = ProbeJob(process)
        messages: queue.Queue = queue.Queue()
        def reader():
            try:
                for line in process.stdout:
                    messages.put(line)
            finally:
                messages.put(None)
        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()
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
            had_error = sys.exc_info()[0] is not None
            try:
                try:
                    process.stdin.close()
                except OSError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    job.close()
                except (OSError, subprocess.SubprocessError) as exc:
                    logger().warning("Probe job cleanup failed: %s", type(exc).__name__)
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                reader_thread.join(timeout=3)
                if not reader_thread.is_alive():
                    process.stdout.close()
                else:
                    raise RuntimeError("Codex 校验进程的输出仍被占用，配置未修改。")
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                logger().warning("Probe shutdown failed: %s", type(exc).__name__)
                if not had_error:
                    raise RuntimeError("Codex 配置校验进程未能正常退出，配置未修改。") from exc
