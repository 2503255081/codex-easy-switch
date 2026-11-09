import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_provider_launcher.desktop_state import OWNER, STATE, SESSION, prepare_view


def fixture():
    return {
        OWNER: {"owner": "personal-owner", "inactive": {"other-owner": {"atoms": {}, "globals": {"private": "keep"}}}},
        "electron-persisted-atom-state": {"account-workspaces": ["personal-cloud"], "sidebar-project-list-expanded-v1": False},
        "local-projects": {
            "folder": {"id": "folder", "name": "Local", "rootPaths": ["/work"], "createdAt": 1, "updatedAt": 1},
            "g-p-cloud": {"id": "g-p-cloud", "name": "Cloud", "rootPaths": ["/cloud"], "chatGptBacking": {"accountId": "personal"}},
        },
        "thread-project-assignments": {
            "local-chat": {"projectKind": "local", "projectId": "folder"},
            "cloud-chat": {"projectKind": "local", "projectId": "g-p-cloud"},
        },
        "project-order": ["g-p-cloud", "folder"],
        "project-appearances": {"folder": {"color": "red"}},
        "queued-follow-ups": {"cloud-chat": ["do not replay"]},
        "app-server-project-id-by-legacy-project-id-by-host": {"local:/fixture": {"folder": "server-folder", "g-p-cloud": "server-cloud"}},
        "electron-main-window-bounds": {"width": 1000},
    }


class DesktopStateTests(unittest.TestCase):
    def test_api_view_keeps_local_projects_excludes_cloud_and_restores_exact_personal_view(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            original = fixture()
            (home / STATE).write_text(json.dumps(original), encoding="utf-8")
            auth = home / "auth.json"
            auth.write_bytes(b"untouched auth")
            change = prepare_view(home, True, home / "backups")
            api = json.loads((home / STATE).read_bytes())
            self.assertNotIn(OWNER, api)
            self.assertEqual(set(api["local-projects"]), {"folder"})
            self.assertEqual(api["project-order"], ["folder"])
            self.assertEqual(set(api["thread-project-assignments"]), {"local-chat"})
            self.assertNotIn("queued-follow-ups", api)
            self.assertNotIn("personal-cloud", json.dumps(api))
            self.assertEqual(api["app-server-project-id-by-legacy-project-id-by-host"], {"local:/fixture": {"folder": "server-folder"}})
            self.assertIsNone(prepare_view(home, True, home / "backups"))
            prepare_view(home, False, home / "backups")
            self.assertEqual(json.loads((home / STATE).read_bytes()), original)
            self.assertFalse((home / SESSION).exists())
            self.assertEqual(auth.read_bytes(), b"untouched auth")
            self.assertEqual(len(list((home / "backups").glob("desktop-view-*.json"))), 2)

    def test_api_local_edits_return_to_personal_without_changing_cloud_or_other_account(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d); original = fixture()
            (home / STATE).write_text(json.dumps(original), encoding="utf-8")
            prepare_view(home, True, home / "backups")
            api = json.loads((home / STATE).read_bytes())
            api["local-projects"]["folder"]["name"] = "Renamed"
            api["local-projects"]["new"] = {"id": "new", "name": "New", "rootPaths": ["/new"]}
            api["project-order"].insert(0, "new")
            api["thread-project-assignments"]["new-chat"] = {"projectKind": "local", "projectId": "new"}
            (home / STATE).write_text(json.dumps(api), encoding="utf-8")
            prepare_view(home, False, home / "backups")
            restored = json.loads((home / STATE).read_bytes())
            self.assertEqual(restored["local-projects"]["folder"]["name"], "Renamed")
            self.assertIn("new", restored["project-order"])
            self.assertEqual(restored[OWNER], original[OWNER])
            self.assertEqual(restored["local-projects"]["g-p-cloud"], original["local-projects"]["g-p-cloud"])
            self.assertEqual(restored["thread-project-assignments"]["cloud-chat"], original["thread-project-assignments"]["cloud-chat"])
            self.assertIn("new-chat", restored["thread-project-assignments"])

    def test_failed_launch_rolls_back_both_files_and_wont_overwrite_external_edits(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d); raw = json.dumps(fixture()).encode()
            (home / STATE).write_bytes(raw)
            change = prepare_view(home, True, home / "backups")
            change.rollback()
            self.assertEqual((home / STATE).read_bytes(), raw)
            self.assertFalse((home / SESSION).exists())
            change = prepare_view(home, True, home / "backups")
            (home / STATE).write_bytes(b"external edit")
            with self.assertRaises(RuntimeError):
                change.rollback()
            self.assertEqual((home / STATE).read_bytes(), b"external edit")

    def test_different_account_is_not_overwritten_and_api_only_installs_are_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d); state = fixture(); state.pop(OWNER)
            raw = json.dumps(state).encode(); (home / STATE).write_bytes(raw)
            self.assertIsNone(prepare_view(home, True, home / "backups"))
            self.assertEqual((home / STATE).read_bytes(), raw)
            (home / STATE).write_text(json.dumps(fixture()), encoding="utf-8")
            prepare_view(home, True, home / "backups")
            changed = json.loads((home / STATE).read_bytes())
            changed[OWNER] = {"owner": "someone-else", "inactive": {}}
            raw = json.dumps(changed).encode(); (home / STATE).write_bytes(raw)
            with self.assertRaises(ValueError):
                prepare_view(home, False, home / "backups")
            self.assertEqual((home / STATE).read_bytes(), raw)

    def test_backup_failure_does_not_change_the_view(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d); raw = json.dumps(fixture()).encode()
            (home / STATE).write_bytes(raw)
            with patch("codex_provider_launcher.desktop_state.atomic_write", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    prepare_view(home, True, home / "backups")
            self.assertEqual((home / STATE).read_bytes(), raw)
            self.assertFalse((home / SESSION).exists())
