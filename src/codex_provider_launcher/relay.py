"""Loopback credential boundary for showing a saved account with an API route."""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import re
import threading
import time
import urllib.error
import urllib.request

from .diagnostics import logger, protect
from .network import build_api_opener
from .profiles import Profile


class AccountRelay:
    """Accept a local credential; send only the API key to the selected endpoint.

    Account login/limits continue using the official client and official account
    service. This proxy never forwards an incoming Authorization or account ID.
    """
    def __init__(self, profile: Profile, secret: str, config_home: Path):
        profile.validate()
        self.profile, self.secret, self.config_home = profile, secret, config_home
        self.master = "launcher-" + secrets.token_hex(32)
        self.server = self.thread = None
        self.url = None
        self.lock = threading.Lock()
        self.last_request = None
        protect(secret)
        protect(self.master)

    def authenticated(self, header: str) -> bool:
        if not header.startswith("Bearer "):
            return False
        value = header[7:]
        if hmac.compare_digest(value, self.master):
            return True
        try:
            auth = json.loads((self.config_home / "auth.json").read_text(encoding="utf-8"))
            token = (auth.get("tokens") or {}).get("access_token")
            return auth.get("auth_mode") == "chatgpt" and isinstance(token, str) and hmac.compare_digest(value, token)
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def error(self, status, message):
                raw = json.dumps({"error": {"message": message, "type": "launcher_relay"}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(raw)

            def do_POST(self):
                # Only inference routes are admitted. No arbitrary URL/path proxy.
                suffix = {"/v1/responses": "/responses", "/v1/responses/compact": "/responses/compact"}.get(self.path)
                if suffix is None:
                    self.error(404, "Unsupported local relay route.")
                    return
                if self.headers.get("Origin") is not None or not owner.authenticated(self.headers.get("Authorization", "")):
                    self.error(401, "Local relay authentication failed.")
                    return
                if self.headers.get("Transfer-Encoding"):
                    self.error(400, "A bounded Content-Length is required.")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 64 * 1024 * 1024:
                        raise ValueError()
                    raw = self.rfile.read(length)
                    document = json.loads(raw)
                    if not isinstance(document, dict):
                        raise ValueError()
                except (ValueError, OSError):
                    self.error(400, "Invalid or oversized inference request.")
                    return
                # Copy a bounded allowlist, never account credentials, cookies,
                # arbitrary upstream URLs or identity-bearing account headers.
                allowed = {"accept", "content-type", "openai-beta", "version", "originator", "session_id", "conversation_id",
                           "x-codex-turn-state", "x-codex-turn-metadata", "x-codex-client-metadata"}
                headers = {k: v for k, v in self.headers.items() if k.lower() in allowed}
                headers.update({"Authorization": "Bearer " + owner.secret,
                                "x-opencodex-api-key": owner.secret, "Accept-Encoding": "identity"})
                request = urllib.request.Request(owner.profile.base_url + suffix, data=raw, headers=headers, method="POST")
                started = False
                try:
                    try:
                        upstream = build_api_opener().open(request, timeout=600)
                    except urllib.error.HTTPError as exc:
                        upstream = exc
                    with upstream:
                        status = upstream.code
                        self.send_response(status)
                        response_headers = {"content-type", "content-encoding", "retry-after", "request-id", "x-request-id"}
                        for key, value in upstream.headers.items():
                            if key.lower() in response_headers or key.lower().startswith("x-codex-"):
                                self.send_header(key, value)
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        started = True
                        self.close_connection = True
                        with owner.lock:
                            owner.last_request = {"at": time.time(), "model": document.get("model"),
                                                  "service_tier": document.get("service_tier"), "status": status}
                        model = document.get("model")
                        tier = document.get("service_tier")
                        logger().info("relay request model=%s service_tier=%s status=%s",
                                      model if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", model) else "unknown",
                                      tier if tier in ("default", "auto", "priority", "fast", "flex", "ultrafast", None) else "unknown", status)
                        while chunk := upstream.read1(65536):
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except (OSError, urllib.error.URLError) as exc:
                    logger().warning("local relay upstream unavailable type=%s", type(exc).__name__)
                    if not started:
                        self.error(502, "The selected API endpoint is unavailable; no personal-account fallback was attempted.")

            def do_GET(self):
                self.error(404, "Unsupported local relay route.")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=3)


class RelayGroup:
    def __init__(self, *adapters):
        self.adapters = [item for item in adapters if item is not None]

    def __exit__(self, *_):
        for adapter in reversed(self.adapters):
            adapter.__exit__(None, None, None)
