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
        import keyring
        import subprocess
        vault = CredentialStore()
        credential_id = "package-test-" + str(uuid.uuid4())
        try:
            vault.put(credential_id, "temporary-package-test-value")
            assert vault.get(credential_id) == "temporary-package-test-value"
        finally:
            if vault.get(credential_id) is not None:
                vault.delete(credential_id)
        app = Launcher()
        app.withdraw()
        try:
            app.open_settings("relay")
            app.update_idletasks()
            assert app.test_button.cget("text") == "测试连接并获取模型"
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
                    "tk": True, "bridge_persisted": True}
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
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        try:
            self_test(sys.argv[2])
        except Exception as exc:
            Path(sys.argv[2]).write_text(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)}), encoding="utf-8")
            sys.exit(1)
    else:
        from codex_provider_launcher.app import main
        main()
