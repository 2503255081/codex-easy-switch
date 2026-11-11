"""GUI entry point used only by the portable Windows builds."""
import ctypes
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import uuid


def self_test(report):
    # Exercise packaged Tcl/Tk, the system credential backend, and the persistent
    # bridge using an isolated launcher home, without opening the user's Codex.
    with tempfile.TemporaryDirectory(prefix="launcher-package-test-") as directory:
        os.environ["APPDATA"] = directory
        from codex_provider_launcher.app import Launcher
        from codex_provider_launcher.profiles import CredentialStore
        from codex_provider_launcher.platforms import ensure_windows_bridge, system_powershell
        from codex_provider_launcher.history import migrate_routes, restore_routes
        import keyring
        import subprocess
        import sqlite3
        history_home = Path(directory) / "history-fixture"
        history_home.mkdir()
        conn = sqlite3.connect(history_home / "state_5.sqlite")
        try:
            conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT, history_mode TEXT)")
            conn.execute("INSERT INTO threads VALUES ('fixture','openai','paginated')")
            conn.commit()
        finally:
            conn.close()
        route_backup = migrate_routes(history_home, "launcher_custom", Path(directory) / "backups")
        assert route_backup and restore_routes(history_home, route_backup) == 1
        vault = CredentialStore()
        credential_id = "package-test-" + str(uuid.uuid4())
        try:
            vault.put(credential_id, "temporary-package-test-value")
            assert vault.get(credential_id) == "temporary-package-test-value"
        finally:
            if vault.get(credential_id) is not None:
                vault.delete(credential_id)
        app = Launcher(check_updates=False)
        app.withdraw()
        try:
            from codex_provider_launcher import __version__
            assert app.update_text.get() == "v" + __version__
            assert app.update_button.cget("text") == "检查更新"
            app.open_settings("relay")
            app.update_idletasks()
            assert app.test_button.cget("text") == "测试连接并获取模型"
            assert "独立密钥" in app.quota_note.cget("text")
            assert app.quota_compatibility.get()
            assert app.quota_check.cget("text") == "同时显示个人账号额度"
            app.mode.set("doubao")
            app.select_mode()
            assert app.model_choice.get().startswith("doubao-")
            assert "doubao-seed-2-0-mini" in app.more_models.get()
            app.plan.set("Coding Plan")
            app.select_plan()
            assert "glm-5.3" in app.more_models.get()
            assert app.test_button.cget("text") == "测试模型（可能产生费用）"
            app.mode.set("qwen")
            app.select_mode()
            assert app.url_label.cget("text") == "工作空间 ID（可选）"
            app.mode.set("qianwen")
            app.select_mode()
            assert app.model_choice.get() == "qwen3.8-max"
            assert "qwen3.8-flash" in app.more_models.get()
            assert app.key_link.cget("text") == "千问 AI 密钥页 ↗"
            app.mode.set("deepseek")
            app.select_mode()
            assert tuple(app.model_entry.cget("values")) == ("deepseek-flash", "deepseek-v4-pro")
            app.mode.set("qwen")
            app.select_mode()
            assert app.key_link.cget("text") == "百炼密钥页 ↗"
            assert app.fetch_models_button.cget("text") == "读取授权模型"
            assert "qwen3.8-flash" in app.more_models.get()
            app.mode.set("relay")
            app.select_mode()
            assert app.name_label.cget("text") == "按钮名称"
            assert app.custom_picker.current() == 0
            bridge = ensure_windows_bridge()
            assert bridge.read_bytes().startswith(b"MZ")
            assert Path(directory) in bridge.parents
            result = subprocess.run([system_powershell(), "-NoProfile", "-NonInteractive", "-Command", "[IntPtr]::Size"],
                                    capture_output=True, text=True, timeout=20,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            assert result.returncode == 0
            data = {"ok": True, "launcher_bits": struct.calcsize("P") * 8,
                    "powershell_bits": int(result.stdout.strip()) * 8,
                    "credential_backend": type(keyring.get_keyring()).__name__,
                    "tk": True, "bridge_persisted": True, "history_routes": True}
        finally:
            app.destroy()
            import logging
            logging.shutdown()
    Path(report).write_text(json.dumps(data), encoding="utf-8")


if __name__ == "__main__":
    # PyInstaller's DLL lookup path must not leak into PowerShell or Codex.
    # Import GUI dependencies first, then restore the normal Windows DLL path.
    import tkinter
    import keyring
    ctypes.windll.kernel32.SetDllDirectoryW(None)
    if len(sys.argv) == 3 and sys.argv[1] in ("--self-test", "--self-test-https"):
        try:
            if sys.argv[1] == "--self-test-https":
                from codex_provider_launcher.network import https_self_test
                https_self_test(sys.argv[2])
            else:
                self_test(sys.argv[2])
        except Exception as exc:
            Path(sys.argv[2]).write_text(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)}), encoding="utf-8")
            sys.exit(1)
    else:
        from codex_provider_launcher.app import main
        main()
