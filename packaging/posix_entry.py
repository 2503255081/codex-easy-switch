"""Entrypoint for portable Linux and macOS distributions."""
import json
import os
from pathlib import Path
import sys
import tempfile


def self_test(report):
    import subprocess
    import tkinter
    from codex_provider_launcher.config import candidate
    from codex_provider_launcher.profiles import new_profile
    import tomlkit
    # Tcl loads even on a headless CI runner. GUI rendering is checked separately.
    version = tkinter.Tcl().eval("info patchlevel")
    with tempfile.TemporaryDirectory(prefix="launcher-posix-check-") as directory:
        root = Path(directory)
        fake = root / "fake-codex"
        fake.write_text("#!/bin/sh\ncat\n", encoding="utf-8")
        fake.chmod(0o700)
        env = dict(os.environ)
        env.update(CODEX_SWITCH_REAL_CLI=str(fake), CODEX_SWITCH_PROVIDER="launcher_custom",
                   CODEX_SWITCH_LAST_MODEL_FILE=str(root / "last"), PYINSTALLER_RESET_ENVIRONMENT="1")
        request = {"id": 1, "method": "thread/resume", "params": {"modelProvider": "openai", "model": "sample-model"}}
        process = subprocess.run([sys.executable, "--bridge", "app-server"], env=env,
                                 input=json.dumps(request) + "\n", text=True, capture_output=True, timeout=30)
        if process.returncode:
            raise RuntimeError("Bridge self-test failed: " + process.stderr[-500:])
        result = json.loads(process.stdout)
        assert result["params"]["modelProvider"] == "launcher_custom"
        assert (root / "last").read_text() == "sample-model"
        profile = new_profile("test", "responses", "https://example.com/v1", "sample-model")
        document = tomlkit.parse(candidate(b'[projects."/fixture"]\ntrust_level="trusted"\n', profile).decode())
        assert document["projects"]["/fixture"]["trust_level"] == "trusted"
    Path(report).write_text(json.dumps({"ok": True, "platform": sys.platform, "tcl": version,
                                       "bridge": True, "config": True}), encoding="utf-8")


def gui_self_test(report):
    import logging
    from codex_provider_launcher import app as launcher_app, profiles
    with tempfile.TemporaryDirectory(prefix="launcher-posix-gui-") as directory:
        profiles.data_dir = lambda: Path(directory)
        launcher_app.data_dir = lambda: Path(directory)
        app = launcher_app.Launcher()
        try:
            app.update()
            app.open_settings("relay")
            app.update()
            if len(app.launch_buttons) != 1 or str(app.launch_buttons[0].cget("text")) != "个人账号":
                raise RuntimeError("unexpected initial launch buttons")
            if str(app.test_button.cget("text")) != "测试连接并获取模型":
                raise RuntimeError("connection test button not available")
            if str(app.effort_entry.cget("state")) != "readonly":
                raise RuntimeError("reasoning selector is not readonly")
            for mode, expected in (("doubao", ("minimal", "low", "medium", "high")),
                                   ("qwen", ("none", "low", "medium", "xhigh")),
                                   ("qianwen", ("none", "low", "medium", "xhigh"))):
                app.mode.set(mode)
                app.select_mode()
                choices = tuple(app.effort_entry.cget("values"))
                if choices != ("跟随当前设置", *expected):
                    raise RuntimeError("unexpected reasoning options for " + mode)
            Path(report).write_text(json.dumps({"ok": True, "gui": True}), encoding="utf-8")
        finally:
            app.destroy()
            logging.shutdown()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--bridge":
        sys.argv.pop(1)
        from codex_provider_launcher.bridge import main
        sys.exit(main())
    if len(sys.argv) == 3 and sys.argv[1] in ("--self-test", "--self-test-gui"):
        try:
            (self_test if sys.argv[1] == "--self-test" else gui_self_test)(sys.argv[2])
        except Exception as exc:
            Path(sys.argv[2]).write_text(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)}), encoding="utf-8")
            sys.exit(1)
    else:
        from codex_provider_launcher.app import main
        main()
