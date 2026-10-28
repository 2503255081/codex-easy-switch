"""Forward app-server calls while choosing the provider for existing threads."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading


def rewrite(line: bytes, provider: str, fixed_model: str = "") -> bytes:
    try:
        obj = json.loads(line)
        if not isinstance(obj, dict) or obj.get("method") not in ("thread/start", "thread/resume", "thread/fork", "turn/start"):
            return line
        if fixed_model and (provider != "launcher_custom" or fixed_model != "deepseek-flash"):
            raise ValueError("invalid fixed model")
        is_thread = obj.get("method") != "turn/start"
        params = obj.setdefault("params", {})
        if not isinstance(params, dict):
            raise ValueError("invalid thread params")
        if is_thread:
            params["modelProvider"] = provider
        selected = params.get("model")
        config = params.get("config")
        if not selected and isinstance(config, dict):
            selected = config.get("model")
        last_file = os.environ.get("CODEX_SWITCH_LAST_MODEL_FILE")
        if provider == "launcher_custom" and not fixed_model and last_file and isinstance(selected, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", selected):
            try:
                with open(last_file, "w", encoding="utf-8") as stream:
                    stream.write(selected)
            except OSError:
                pass
        if not is_thread and not fixed_model:
            return line
        if fixed_model and (selected is not None or is_thread):
            selected = selected if selected in ("deepseek-flash", "deepseek-v4-pro") else fixed_model
            params["model"] = selected
        if isinstance(params.get("config"), dict):
            if is_thread:
                params["config"]["model_provider"] = provider
            if fixed_model and selected is not None:
                params["config"]["model"] = selected
        return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    except (ValueError, UnicodeError):
        raise ValueError("Cannot safely route this app-server request")


def main():
    target = os.environ.get("CODEX_SWITCH_REAL_CLI", "")
    provider = os.environ.get("CODEX_SWITCH_PROVIDER", "")
    if not target or not os.path.isfile(target) or provider not in ("openai", "launcher_custom"):
        print("Provider bridge is not configured.", file=sys.stderr)
        return 1
    args = sys.argv[1:]
    server = "app-server" in args and not any(x in args for x in ("generate-ts", "generate-json-schema", "--help"))
    env = os.environ.copy()
    env["CODEX_CLI_PATH"] = target
    process = subprocess.Popen([target, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    def copy(source, destination):
        try:
            while chunk := os.read(source.fileno(), 65536):
                destination.write(chunk)
                destination.flush()
        except (BrokenPipeError, OSError):
            pass
    def send():
        try:
            if server:
                for line in sys.stdin.buffer:
                    process.stdin.write(rewrite(line, provider, os.environ.get("CODEX_SWITCH_FIXED_MODEL", "")))
                    process.stdin.flush()
            else:
                copy(sys.stdin.buffer, process.stdin)
        except (ValueError, BrokenPipeError, OSError):
            process.kill()
        finally:
            process.stdin.close()
    out = threading.Thread(target=copy, args=(process.stdout, sys.stdout.buffer), daemon=True)
    err = threading.Thread(target=copy, args=(process.stderr, sys.stderr.buffer), daemon=True)
    inp = threading.Thread(target=send, daemon=True)
    for thread in (out, err, inp):
        thread.start()
    try:
        code = process.wait()
        out.join()
        err.join()
        return code
    finally:
        if process.poll() is None:
            process.kill()


if __name__ == "__main__":
    sys.exit(main())
