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


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--bridge":
        sys.argv.pop(1)
        from codex_provider_launcher.bridge import main
        sys.exit(main())
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        try:
            self_test(sys.argv[2])
        except Exception as exc:
            Path(sys.argv[2]).write_text(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)}), encoding="utf-8")
            sys.exit(1)
    else:
        from codex_provider_launcher.app import main
        main()
