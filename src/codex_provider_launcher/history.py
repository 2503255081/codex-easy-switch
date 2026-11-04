"""Back up and migrate local paginated thread routes while Desktop is closed.

Only the provider column in the state index is changed. Conversation history,
thread IDs, timestamps, titles, permissions and account files are not rewritten.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import atomic_write

ROUTES = ("openai", "launcher_custom", "opencodex_desktop_pool")


def _database(home: Path) -> Path | None:
    candidates = []
    for path in home.glob("state_*.sqlite"):
        match = re.fullmatch(r"state_(\d+)\.sqlite", path.name)
        if match and path.is_file():
            candidates.append((int(match[1]), path))
    return max(candidates, key=lambda pair: pair[0])[1] if candidates else None


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5)
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(threads)")}
    if not {"id", "model_provider", "history_mode"}.issubset(columns) or columns["id"][5] != 1:
        conn.close()
        raise ValueError("当前 Codex 对话索引格式未知，已停止切换；请查看日志。")
    return conn


def migrate_routes(home: Path, target: str, backups: Path) -> Path | None:
    """Save exact old routes before an atomic, narrowly scoped index update."""
    if target not in ("openai", "launcher_custom"):
        raise ValueError("无效的启动提供商。")
    path = _database(home)
    if path is None:
        return None
    if path.resolve().parent != home.resolve():
        raise ValueError("Codex 对话索引位置异常，已停止切换。")
    conn = _open(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, model_provider FROM threads WHERE history_mode='paginated' "
            "AND model_provider IN (?,?,?) AND model_provider != ? ORDER BY id",
            (*ROUTES, target),
        ).fetchall()
        if not rows:
            conn.rollback()
            return None
        record = {
            "version": 1, "database": str(path.resolve()), "target": target,
            "created_at": datetime.now(timezone.utc).isoformat(), "rows": rows,
        }
        backup = backups / ("history-routes-" + uuid.uuid4().hex + ".json")
        atomic_write(backup, json.dumps(record, ensure_ascii=False).encode("utf-8"))
        conn.executemany(
            "UPDATE threads SET model_provider=? WHERE id=? AND model_provider=? AND history_mode='paginated'",
            [(target, tid, previous) for tid, previous in rows],
        )
        conn.commit()
        return backup
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def restore_routes(home: Path, backup: Path) -> int:
    """Restore a saved index change, refusing later incompatible route edits."""
    record = json.loads(backup.read_text(encoding="utf-8"))
    path = _database(home)
    if (path is None or record.get("version") != 1
            or record.get("database") != str(path.resolve()) or path.resolve().parent != home.resolve()
            or record.get("target") not in ("openai", "launcher_custom")):
        raise ValueError("对话路由恢复记录与当前 Codex 不匹配。")
    rows = record.get("rows")
    if (not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != 2
            or not isinstance(row[0], str) or row[1] not in ROUTES for row in rows)
            or len({row[0] for row in rows}) != len(rows)):
        raise ValueError("对话路由恢复记录无效。")
    conn = _open(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        restored = 0
        for tid, previous in rows:
            current = conn.execute("SELECT model_provider, history_mode FROM threads WHERE id=?", (tid,)).fetchone()
            if current is None:  # Do not recreate a chat the user deleted.
                continue
            if current[1] != "paginated" or current[0] not in (record["target"], previous):
                raise ValueError("对话路由在切换后已被修改，已停止恢复。")
            conn.execute("UPDATE threads SET model_provider=? WHERE id=?", (previous, tid))
            restored += 1
        conn.commit()
        return restored
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
