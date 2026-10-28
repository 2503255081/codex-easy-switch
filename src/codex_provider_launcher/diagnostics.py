"""Bounded local diagnostics. Never log request bodies or credential values."""
from __future__ import annotations

import logging
import re
import threading
from logging.handlers import RotatingFileHandler

_secrets: set[str] = set()
_lock = threading.RLock()
_logger = None


def protect(secret: str | None) -> None:
    if secret:
        with _lock:
            _secrets.add(secret)


def redact(value: str) -> str:
    with _lock:
        for secret in sorted(_secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)\b(?:sk-|ocx_data_)[a-z0-9_-]+", "[REDACTED]", value)
    value = re.sub(r"(?i)(Bearer\s+)[^\s\"',;]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", value)
    value = re.sub(r"(?i)([?&](?:key|api_key|token|access_token)=)[^&\s]+", r"\1[REDACTED]", value)
    return value


def log_path():
    from .profiles import data_dir
    return data_dir() / "logs" / "launcher.log"


class RedactionFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def logger():
    global _logger
    with _lock:
        if _logger is None:
            result = logging.getLogger("codex-provider-launcher")
            result.setLevel(logging.INFO)
            result.propagate = False
            try:
                path = log_path()
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
                import os
                if os.name != "nt":
                    path.chmod(0o600)
                handler.addFilter(RedactionFilter())
                handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            except OSError:
                handler = logging.NullHandler()
            result.addHandler(handler)
            _logger = result
        return _logger


def read_logs():
    path = log_path()
    if not path.exists():
        return "暂无日志。"
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 256 * 1024))
        return redact(stream.read().decode("utf-8", errors="replace"))
