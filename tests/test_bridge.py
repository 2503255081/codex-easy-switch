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
