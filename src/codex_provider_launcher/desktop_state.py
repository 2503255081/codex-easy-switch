"""Keep local folder projects across Desktop's ChatGPT/API identity boundary.

Recent Desktop versions clear account-scoped renderer state for an API identity.
Use a local-only view while the launcher selects an API, and restore the original
account view on return. Never fabricate an account or copy cloud project backing.
All writes run while Desktop is closed, with an exact rollback journal.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .config import atomic_write

STATE = ".codex-global-state.json"
SESSION = ".codex-easy-switch-personal-view.json"
OWNER = "electron-account-renderer-state-v1"
ATOMS = "electron-persisted-atom-state"
# Account-scoped globals in Desktop 26.1103. Account snapshots and cloud backing
# are kept out of the API view; unrelated machine preferences are left intact.
SCOPED = (
    "open-in-target-preferences", "worktree-repository-preferences",
    "desktop-app-beta-preferences-by-account-id", "active-workspace-roots",
    "electron-saved-workspace-roots", "electron-workspace-root-labels",
    "local-projects", "app-server-project-id-by-legacy-project-id-by-host",
    "app-server-projects-migration-by-host", "app-server-pending-project-deletions-by-host",
    "selected-project", "project-writable-roots", "project-appearances", "project-files",
    "pinned-thread-ids", "app-server-migrated-pinned-thread-ids-by-host",
    "pinned-project-ids", "sidebar-project-thread-orders", "sidebar-thread-metadata",
    "thread-project-assignments", "thread-project-membership-host-ids",
    "thread-writable-roots", "thread-workspace-root-hints",
    "thread-projectless-output-directories", "projectless-thread-ids",
    "selected-remote-host-id", "remote-projects", "active-remote-project-id",
    "project-order", "connection-group-order", "remote-cwds-by-host-and-workspace",
    "host-id-remote-control-allowed", "added-remote-control-env-ids",
    "remote-project-connection-backfill-completed", "remote-connection-auto-connect-by-host-id",
    "queued-follow-ups",
)
PROJECT_MAPS = ("project-appearances", "project-files", "project-writable-roots", "sidebar-project-thread-orders")


def _encode(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def local_view(state: dict) -> dict:
    """Copy only ordinary local projects and their explicit local assignments."""
    projects = {
        pid: {key: copy.deepcopy(p[key]) for key in ("id", "name", "rootPaths", "createdAt", "updatedAt") if key in p}
        for pid, p in (state.get("local-projects") or {}).items()
        if isinstance(p, dict) and p.get("id") == pid and not pid.startswith("g-p-")
        and not any(p.get(k) for k in ("chatGptBacking", "chatGptBackingCreation", "retiredChatGptBacking"))
        and isinstance(p.get("rootPaths"), list) and p["rootPaths"]
    }
    result = {"local-projects": projects}
    for key in PROJECT_MAPS:
        if isinstance(state.get(key), dict):
            result[key] = {k: copy.deepcopy(v) for k, v in state[key].items() if k in projects}
    for key in ("project-order", "pinned-project-ids"):
        result[key] = [x for x in state.get(key, []) if x in projects]
    result["thread-project-assignments"] = {
        tid: copy.deepcopy(a) for tid, a in (state.get("thread-project-assignments") or {}).items()
        if isinstance(a, dict) and a.get("projectKind") == "local" and a.get("projectId") in projects
    }
    selected = state.get("selected-project")
    if isinstance(selected, dict) and selected.get("projectId") in projects:
        result["selected-project"] = copy.deepcopy(selected)
    roots = {p for project in projects.values() for p in project["rootPaths"] if isinstance(p, str)}
    for key in ("active-workspace-roots", "electron-saved-workspace-roots"):
        result[key] = [p for p in state.get(key, []) if p in roots]
    result["electron-workspace-root-labels"] = {
        k: v for k, v in (state.get("electron-workspace-root-labels") or {}).items() if k in roots
    }
    # Preserve exact app-server mappings, so Desktop does not import duplicates.
    for key in ("app-server-project-id-by-legacy-project-id-by-host",):
        result[key] = {host: {k: v for k, v in mapping.items() if k in projects}
                       for host, mapping in (state.get(key) or {}).items() if isinstance(mapping, dict)}
    return result


def _merge_local(personal: dict, initial: dict, current: dict) -> dict:
    """Bring explicit local API-view edits back without changing cloud state."""
    result = copy.deepcopy(personal)
    latest = local_view(current)
    for key in ("local-projects", *PROJECT_MAPS, "thread-project-assignments",
                "electron-workspace-root-labels"):
        before, after = initial.get(key, {}), latest.get(key, {})
        for item in set(before) | set(after):
            if before.get(item) == after.get(item):
                continue
            target = result.setdefault(key, {})
            if item in after:
                target[item] = copy.deepcopy(after[item])
            else:
                target.pop(item, None)
    for key in ("project-order", "pinned-project-ids", "active-workspace-roots", "electron-saved-workspace-roots"):
        before, after = initial.get(key, []), latest.get(key, [])
        if before != after:
            result[key] = [x for x in personal.get(key, []) if x not in before and x not in after] + after
    mappings = "app-server-project-id-by-legacy-project-id-by-host"
    for host, values in latest.get(mappings, {}).items():
        result.setdefault(mappings, {}).setdefault(host, {}).update(values)
    return result


class ViewChange:
    def __init__(self, home: Path, before: dict[Path, bytes | None], after: dict[Path, bytes | None]):
        self.home, self.before, self.after = home, before, after

    def rollback(self):
        for path, expected in self.after.items():
            if _read(path) != expected:
                raise RuntimeError("项目视图在启动期间被修改，已保留恢复记录；请查看日志。")
        for path, raw in self.before.items():
            if raw is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, raw)


def prepare_view(home: Path, api: bool, backups: Path) -> ViewChange | None:
    path, session = home / STATE, home / SESSION
    raw, journal = _read(path), _read(session)
    if raw is None:
        return None
    state = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(state, dict):
        raise ValueError("Codex 项目视图格式不受支持，未修改。")
    account = state.get(OWNER)
    record = json.loads(journal) if journal else None
    if record and (record.get("version") != 1 or record.get("home") != str(home.resolve())):
        raise ValueError("项目视图恢复记录与当前 Codex 不匹配。")
    if not api and not record:
        return None
    if record and isinstance(account, dict) and account.get("owner") not in (None, record["owner"]):
        raise ValueError("检测到不同的个人账号，请先恢复原账号或手动检查项目视图备份。")
    if api:
        if record and not account:
            return None  # Repeated API launches must preserve edits made in that view.
        if not record:
            if not account:
                return None  # API-only installs already use an ordinary local view.
            if not isinstance(account, dict) or not account.get("owner"):
                raise ValueError("个人项目位于未激活的账号视图，请先用个人账号启动一次，再切换 API。")
            record = {"version": 1, "home": str(home.resolve()), "owner": account["owner"],
                      "personal": state, "initial": local_view(state)}
        updated = {k: copy.deepcopy(v) for k, v in state.items() if k not in SCOPED and k not in (OWNER, ATOMS)}
        updated.update(record["initial"])
        # No fabricated API account owner; the official client sees an API identity.
        updated[ATOMS] = {"sidebar-project-list-expanded-v1": True}
        new_journal = _encode(record)
    else:
        # A crash between journaling and the first write leaves the original view.
        updated = state if isinstance(account, dict) and account.get("owner") == record["owner"] else _merge_local(record["personal"], record["initial"], state)
        new_journal = None
    new_raw = _encode(updated)
    backup = backups / ("desktop-view-" + hashlib.sha256(raw).hexdigest() + ".json")
    if not backup.exists():
        atomic_write(backup, raw)
    before, after = {path: raw, session: journal}, {path: new_raw, session: new_journal}
    if _read(path) != raw or _read(session) != journal:
        raise RuntimeError("项目视图在准备期间发生变化，请重试；未覆盖用户修改。")
    try:
        # API journal first; personal restoration first. Both orders recover on retry.
        if new_journal is not None:
            atomic_write(session, new_journal)
        atomic_write(path, new_raw)
        if new_journal is None:
            session.unlink(missing_ok=True)
    except BaseException:
        for p, value in before.items():
            if value is None:
                p.unlink(missing_ok=True)
            else:
                atomic_write(p, value)
        raise
    return ViewChange(home, before, after)
