import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from codex_provider_launcher.profiles import new_profile
from codex_provider_launcher.relay import AccountRelay


class RelayTests(unittest.TestCase):
    def test_credentials_and_fast_body_are_separated_from_account(self):
        seen = []

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                seen.append((self.path, dict(self.headers), raw))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'event: response.completed\ndata: {"ok":true}\n\n')

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                auth = b'{"auth_mode":"chatgpt","tokens":{"access_token":"personal-fixture-only"}}'
                (home / "auth.json").write_bytes(auth)
                profile = new_profile("relay", "responses", f"http://127.0.0.1:{upstream.server_port}/v1", "fixture")
                with AccountRelay(profile, "relay-fixture-only", home) as relay:
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    body = b'{"model":"fixture","service_tier":"fast","stream":true,"input":"fixture"}'
                    for token in ("personal-fixture-only", relay.master):
                        req = urllib.request.Request(relay.url + "/responses", data=body,
                            headers={"Authorization": "Bearer " + token, "ChatGPT-Account-Id": "private-account",
                                     "Cookie": "private-cookie", "Originator": "codex_cli_rs", "Content-Type": "application/json"})
                        with opener.open(req, timeout=5) as response:
                            self.assertIn(b"response.completed", response.read())
                    for path, headers, raw in seen:
                        self.assertEqual(path, "/v1/responses")
                        self.assertEqual(raw, body)
                        normalized = {k.lower(): v for k, v in headers.items()}
                        self.assertEqual(normalized["authorization"], "Bearer relay-fixture-only")
                        self.assertEqual(normalized["x-opencodex-api-key"], "relay-fixture-only")
                        self.assertEqual(normalized["originator"], "codex_cli_rs")
                        for value in ("personal-fixture-only", "private-account", "private-cookie", relay.master):
                            self.assertNotIn(value, json.dumps(headers))
                    self.assertEqual(relay.last_request["service_tier"], "fast")
                    for suffix, token, origin, expected in (("/responses", "wrong", None, 401),
                            ("/responses", relay.master, "https://example.invalid", 401),
                            ("/../responses", relay.master, None, 404)):
                        headers = {"Authorization": "Bearer " + token}
                        if origin:
                            headers["Origin"] = origin
                        with self.assertRaises(urllib.error.HTTPError) as raised:
                            opener.open(urllib.request.Request(relay.url + suffix, data=body, headers=headers), timeout=5)
                        self.assertEqual(raised.exception.code, expected)
                    self.assertEqual(len(seen), 2)
                    self.assertEqual((home / "auth.json").read_bytes(), auth)
        finally:
            upstream.shutdown()
            upstream.server_close()

    def test_upstream_failure_has_no_personal_inference_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            # A closed local port provides a deterministic transport failure.
            upstream = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            port = upstream.server_port
            upstream.server_close()
            profile = new_profile("offline", "responses", f"http://127.0.0.1:{port}/v1", "fixture")
            with AccountRelay(profile, "fixture-key", home) as relay:
                request = urllib.request.Request(relay.url + "/responses", data=b'{"model":"fixture"}',
                    headers={"Authorization": "Bearer " + relay.master})
                with self.assertRaises(urllib.error.HTTPError) as result:
                    urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5)
                self.assertEqual(result.exception.code, 502)
                self.assertIn(b"no personal-account fallback", result.exception.read())
