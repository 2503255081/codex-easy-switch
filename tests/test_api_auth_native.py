"""Optional official CLI test against a loopback-only Responses fixture."""
import base64
import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from codex_provider_launcher.config import candidate
from codex_provider_launcher.probe import clean_env
from codex_provider_launcher.profiles import new_profile
from codex_provider_launcher.relay import AccountRelay
from dataclasses import replace


@unittest.skipUnless(os.environ.get("CODEX_TEST_CLI"), "requires a local official Codex CLI")
class NativeApiAuthTests(unittest.TestCase):
    def test_saved_chatgpt_login_does_not_supply_api_authorization(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                seen.append((self.path, dict(self.headers)))
                message = {"id": "msg_fixture", "type": "message", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": "hello", "annotations": []}]}
                response = {"id": "resp_fixture", "object": "response", "created_at": 1,
                            "status": "completed", "model": "fixture-model", "output": [message],
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
                events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                          {"type": "response.output_item.added", "output_index": 0, "item": message},
                          {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "hello"},
                          {"type": "response.completed", "response": response}]
                raw = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events).encode()
                self.send_response(200); self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory(prefix="launcher-auth-fixture-") as d:
                home = Path(d)
                claims = {"sub": "fixture-user", "email": "fixture@example.invalid",
                          "https://api.openai.com/auth": {"chatgpt_user_id": "fixture-user", "chatgpt_account_id": "fixture-account", "chatgpt_plan_type": "plus"}}
                part = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
                auth = {"auth_mode": "chatgpt", "tokens": {"id_token": "e30." + part + ".", "access_token": "personal-fixture-only",
                        "refresh_token": "refresh-fixture-only", "account_id": "fixture-account"}, "last_refresh": "2026-11-09T00:00:00Z"}
                raw_auth = json.dumps(auth).encode(); (home / "auth.json").write_bytes(raw_auth)
                profile = new_profile("fixture", "responses", f"http://127.0.0.1:{server.server_port}/v1", "fixture-model")
                (home / "config.toml").write_bytes(candidate(b"", profile, personal_auth=True))
                env = clean_env(); env.update(CODEX_HOME=str(home), CODEX_LAUNCHER_API_KEY="api-fixture-only")
                result = subprocess.run([os.environ["CODEX_TEST_CLI"], "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "Reply hello"],
                                        env=env, cwd=home, capture_output=True, timeout=40, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-1500:])
                self.assertTrue(seen)
                for path, headers in seen:
                    self.assertEqual(path, "/v1/responses")
                    self.assertEqual(next(v for k, v in headers.items() if k.lower() == "authorization"), "Bearer api-fixture-only")
                    self.assertNotIn("personal-fixture-only", json.dumps(headers))
                    self.assertNotIn("refresh-fixture-only", json.dumps(headers))
                self.assertEqual((home / "auth.json").read_bytes(), raw_auth)
                seen.clear()
                with AccountRelay(profile, "api-fixture-only", home) as relay:
                    local = replace(profile, base_url=relay.url, model="gpt-6.1-sol")
                    raw_config = candidate(b'', local, local_account_relay=True, fast=True)
                    # service_tier is a root setting, not a provider-table setting.
                    import tomlkit
                    doc = tomlkit.parse(raw_config.decode())
                    doc["service_tier"] = "fast"
                    (home / "config.toml").write_text(tomlkit.dumps(doc), encoding="utf-8")
                    env["CODEX_LAUNCHER_API_KEY"] = relay.master
                    result = subprocess.run([os.environ["CODEX_TEST_CLI"], "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "Reply hello"],
                                            env=env, cwd=home, capture_output=True, timeout=40, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-1500:])
                    self.assertTrue(seen)
                    for path, headers in seen:
                        self.assertEqual(path, "/v1/responses")
                        self.assertEqual(next(v for k, v in headers.items() if k.lower() == "authorization"), "Bearer api-fixture-only")
                        self.assertNotIn("personal-fixture-only", json.dumps(headers))
                    # Codex serializes the Fast UI/config choice as priority.
                    self.assertEqual(relay.last_request["service_tier"], "priority")
                    self.assertEqual((home / "auth.json").read_bytes(), raw_auth)
        finally:
            server.shutdown(); server.server_close()
