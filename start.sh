#!/usr/bin/env bash
# One-command launcher: ./start.sh  (macOS / Linux / Git Bash)
# Auto-detects the project venv and runs scripts/serve.py.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -x "$HERE/.venv/bin/python" ]; then
    PY="$HERE/.venv/bin/python"
elif [ -x "$HERE/.venv/Scripts/python.exe" ]; then
    PY="$HERE/.venv/Scripts/python.exe"
else
    PY="python"
fi

exec "$PY" "$HERE/scripts/serve.py" "$@"
