import json
import unittest
from codex_provider_launcher.bridge import rewrite


class BridgeTests(unittest.TestCase):
    def test_resume_routes_without_changing_text(self):
        original = {"id": 12, "method": "thread/resume", "params": {"threadId": "fixture", "modelProvider": "old", "config": {"model_provider": "old"}}}
        result = json.loads(rewrite((json.dumps(original) + "\n").encode(), "launcher_custom"))
        self.assertEqual(result["params"]["modelProvider"], "launcher_custom")
        self.assertEqual(result["params"]["config"]["model_provider"], "launcher_custom")
        self.assertEqual(result["params"]["threadId"], "fixture")

    def test_other_messages_unchanged(self):
        original = b'{"method":"anything","params":{"modelProvider":"old"}}\n'
        self.assertEqual(rewrite(original, "launcher_custom"), original)

    def test_deepseek_overrides_old_thread_and_turn_models(self):
        for method in ("thread/start", "thread/resume", "thread/fork", "turn/start"):
            original = {"id": 1, "method": method, "params": {"model": "gpt-6.1-sol", "config": {"model": "old"}}}
            result = json.loads(rewrite((json.dumps(original) + "\n").encode(), "launcher_custom", "deepseek-flash"))
            self.assertEqual(result["params"]["model"], "deepseek-flash")
            self.assertEqual(result["params"]["config"]["model"], "deepseek-flash")
            if method != "turn/start":
                self.assertEqual(result["params"]["modelProvider"], "launcher_custom")

    def test_personal_and_relay_do_not_override_turn_model(self):
        line = b'{"method":"turn/start","params":{"model":"example"}}\n'
        self.assertEqual(rewrite(line, "launcher_custom"), line)
        self.assertEqual(rewrite(line, "openai"), line)
