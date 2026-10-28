import unittest
from types import SimpleNamespace

from codex_provider_launcher.app import Launcher


class Variable:
    def __init__(self, value=""):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class Vault:
    def __init__(self):
        self.data = {}
    def get(self, key):
        return self.data.get(key)
    def put(self, key, value):
        self.data[key] = value


class Store:
    def save(self, profiles):
        self.profiles = profiles


class SimpleSetupTests(unittest.TestCase):
    def setup(self, mode, url="", key="", profiles=None, vault=None):
        obj = SimpleNamespace(mode=Variable(mode), url=Variable(url), key=Variable(key),
                              profiles=profiles or [], store=Store(), vault=vault or Vault())
        obj.existing = lambda choice: next((p for p in reversed(obj.profiles)
                            if p.kind == ("responses" if choice == "relay" else "deepseek")), None)
        return obj

    def test_personal_does_not_need_settings(self):
        obj = self.setup("personal")
        self.assertIsNone(Launcher.prepare_profile(obj))
        self.assertFalse(obj.vault.data)

    def test_relay_requires_only_url_and_key(self):
        obj = self.setup("relay", "https://example.com/v1", "fixture-key")
        profile = Launcher.prepare_profile(obj)
        self.assertEqual(profile.model, "auto")
        self.assertEqual(profile.base_url, "https://example.com/v1")
        self.assertEqual(obj.vault.get(profile.id), "fixture-key")
        obj.url.set("https://other.example.com/v1")
        with self.assertRaises(ValueError):
            Launcher.prepare_profile(obj)

    def test_deepseek_requires_only_key_and_reuses_it(self):
        obj = self.setup("deepseek", key="fixture-key")
        profile = Launcher.prepare_profile(obj)
        self.assertEqual(profile.base_url, "https://api.deepseek.com")
        self.assertEqual(profile.model, "deepseek-chat")
        self.assertEqual(Launcher.prepare_profile(obj).id, profile.id)

    def test_main_buttons_launch_or_open_settings(self):
        obj = self.setup("relay", "https://example.com/v1", "fixture-key")
        profile = Launcher.prepare_profile(obj)
        calls = []
        obj.busy = False
        obj.status = Variable()
        obj.start = lambda value: calls.append(("launch", value))
        obj.open_settings = lambda value: calls.append(("settings", value))
        Launcher.launch_mode(obj, "personal")
        Launcher.launch_mode(obj, "relay")
        Launcher.launch_mode(obj, "deepseek")
        self.assertEqual(calls[0], ("launch", None))
        self.assertEqual(calls[1][1].id, profile.id)
        self.assertEqual(calls[2], ("settings", "deepseek"))
