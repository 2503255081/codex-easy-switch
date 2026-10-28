#!/bin/sh
set -eu
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo 'Python 3 is required.' >&2; exit 1; }
test -x .venv/bin/python || python3 -m venv .venv
.venv/bin/python -c 'import tkinter' || { echo 'Python tkinter is required.' >&2; exit 1; }
.venv/bin/python -m pip install '.[chat]'
exec .venv/bin/python -m codex_provider_launcher.app
