import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from codex_provider_launcher.adapter import ChatAdapter
from codex_provider_launcher.profiles import new_profile


@unittest.skipUnless(os.environ.get("TEST_LITELLM"), "requires the optional LiteLLM proxy")
class AdapterIntegration(unittest.TestCase):
    def test_response_routes_to_mock_chat_provider(self):
        seen = []
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.path, self.headers.get("Authorization"), body))
                if body.get("stream"):
                    parts = [
                        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1, "model": "example-model", "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_test", "type": "function", "function": {"name": "lookup", "arguments": '{"q":"hello"}'}}]}, "finish_reason": None}]},
                        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1, "model": "example-model", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                    ]
                    raw = b"".join(("data: " + json.dumps(part) + "\n\n").encode() for part in parts) + b"data: [DONE]\n\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                value = {"id": "chatcmpl-test", "object": "chat.completion", "created": 1,
                         "model": "example-model", "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hello"}}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
                raw = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        profile = new_profile("test", "openai_chat", f"http://127.0.0.1:{server.server_port}/v1", "example-model")
        try:
            with ChatAdapter(profile, "fixture-only-key", os.environ["TEST_LITELLM"]) as adapter:
                from pathlib import Path
                adapter_config = (Path(adapter.temp.name) / "adapter.json").read_text()
                self.assertNotIn("fixture-only-key", adapter_config)
                self.assertNotIn(adapter.master, adapter_config)
                payload = json.dumps({"model": "example-model", "input": "hello"}).encode()
                def request(key):
                    return urllib.request.Request(adapter.url + "/responses", data=payload,
                        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(request("wrong"), timeout=20)
                self.assertIn(denied.exception.code, (400, 401, 403))
                try:
                    with urllib.request.urlopen(request(adapter.master), timeout=30) as response:
                        result = json.load(response)
                except urllib.error.HTTPError as e:
                    self.fail("Adapter returned " + str(e.code) + ": " + e.read().decode()[:1000] + "; upstream=" + repr(seen))
                self.assertEqual(result["object"], "response")
                self.assertEqual(seen[0][0], "/v1/chat/completions")
                self.assertEqual(seen[0][1], "Bearer fixture-only-key")
                payload = json.dumps({"model": "example-model", "input": "hello", "stream": True,
                    "tools": [{"type": "function", "name": "lookup", "description": "test",
                               "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}]}).encode()
                with urllib.request.urlopen(request(adapter.master), timeout=30) as response:
                    events = response.read().decode()
                self.assertIn("response.completed", events)
                self.assertIn("function_call", events)
                self.assertIn("lookup", events)
                self.assertTrue(seen[-1][2]["stream"])
            self.assertIsNotNone(adapter.process.poll())
        finally:
            server.shutdown()
            server.server_close()
