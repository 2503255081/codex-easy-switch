import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from codex_provider_launcher.history import migrate_routes, restore_routes
from codex_provider_launcher.config import candidate, commit, restore_last, atomic_write
from codex_provider_launcher.profiles import new_profile


def fixture(home):
    path = home / 'state_5.sqlite'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT, history_mode TEXT, title TEXT, tokens_used INTEGER)')
    conn.executemany('INSERT INTO threads VALUES (?,?,?,?,?)', [
        ('personal', 'openai', 'paginated', 'keep title', 99),
        ('pool', 'launcher_custom', 'paginated', 'pool title', 81),
        ('old-pool', 'opencodex_desktop_pool', 'paginated', 'old title', 20),
        ('other', 'unrelated_provider', 'paginated', 'other title', 8),
        ('legacy', 'openai', 'legacy', 'legacy title', 5),
    ])
    conn.commit()
    conn.close()
    return path


def routes(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute('SELECT * FROM threads ORDER BY id').fetchall()


class HistoryTests(unittest.TestCase):
    def test_only_known_paginated_routes_change_and_exact_rows_restore(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d); db = fixture(home); original = routes(db)
            rollout = home / 'fixture.jsonl'; rollout.write_bytes(b'unchanged conversation bytes')
            auth = home / 'auth.json'; auth.write_bytes(b'unchanged account bytes')
            backup = migrate_routes(home, 'launcher_custom', home / 'backups')
            changed = {row[0]:row for row in routes(db)}
            self.assertEqual(changed['personal'][1], 'launcher_custom')
            self.assertEqual(changed['old-pool'][1], 'launcher_custom')
            self.assertEqual(changed['legacy'][1], 'openai')
            self.assertEqual(changed['other'][1], 'unrelated_provider')
            for row in original:
                self.assertEqual(changed[row[0]][2:], row[2:])
            self.assertEqual(rollout.read_bytes(), b'unchanged conversation bytes')
            self.assertEqual(auth.read_bytes(), b'unchanged account bytes')
            self.assertEqual(restore_routes(home, backup), 2)
            self.assertEqual(routes(db), original)

    def test_no_database_does_not_create_one(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            self.assertIsNone(migrate_routes(home, 'openai', home / 'backups'))
            self.assertFalse(list(home.iterdir()))

    def test_backup_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);db=fixture(home);original=routes(db)
            with patch('codex_provider_launcher.history.atomic_write', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):migrate_routes(home, 'launcher_custom', home/'backups')
            self.assertEqual(routes(db), original)

    def test_restore_rejects_concurrent_changes_without_partial_revert(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);db=fixture(home)
            backup=migrate_routes(home, 'launcher_custom', home/'backups')
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("UPDATE threads SET model_provider='another' WHERE id='personal'")
                conn.commit()
            before=routes(db)
            with self.assertRaises(ValueError):restore_routes(home, backup)
            self.assertEqual(routes(db), before)

    def test_config_restore_includes_route_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);db=fixture(home);before=routes(db);backups=home/'backups'
            config=home/'config.toml';original=b'model_provider="openai"\nmodel="fixture"\n'
            config.write_bytes(original)
            profile=new_profile('fixture', 'responses','https://example.com/v1','fixture')
            commit(config,original,candidate(original,profile),backups,lambda _:None)
            migration=migrate_routes(home,'launcher_custom',backups)
            record=json.loads((backups/'last-change.json').read_text())
            record['history_routes']=migration.name
            atomic_write(backups/'last-change.json',json.dumps(record).encode())
            restore_last(config,backups)
            self.assertEqual(config.read_bytes(),original)
            self.assertEqual(routes(db),before)

    def test_personal_pool_personal_switch_keeps_same_ids(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);db=fixture(home)
            migrate_routes(home,'launcher_custom',home/'backups')
            migrate_routes(home,'openai',home/'backups')
            selected={row[0]:row[1] for row in routes(db)}
            self.assertEqual([selected[x] for x in ('personal','pool','old-pool')], ['openai']*3)
            self.assertEqual(selected['other'],'unrelated_provider')
