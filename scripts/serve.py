"""Start the agent-memory-lite HTTP service.

Single-command launcher for the FastAPI service that backs the
UserPromptSubmit hook and any non-MCP client. Runs in the foreground —
Ctrl+C to stop.

    python scripts/serve.py

What it does, in order:
1. Bootstraps `<repo>/.agent_memory/memory.db` if missing.
2. Frees port 8765 if a stale instance from a previous run is still
   listening (only kills processes that look like our own service).
3. Calls `python -m agent_memory_lite`.

Set `AGENT_MEMORY_PORT` to override the default 8765.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("AGENT_MEMORY_PORT", "8765"))


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def bootstrap_if_needed() -> None:
    db = REPO_ROOT / ".agent_memory" / "memory.db"
    if db.exists():
        return
    print(f"[serve] bootstrapping {db}")
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "bootstrap_db.py")],
        check=True,
        cwd=str(REPO_ROOT),
    )


def main() -> int:
    bootstrap_if_needed()

    if port_in_use(PORT):
        print(
            f"[serve] port {PORT} is already in use. Either it's already serving "
            f"(curl http://127.0.0.1:{PORT}/health to check), or another process "
            f"is squatting it. Set AGENT_MEMORY_PORT=<other> to use a different "
            f"port.",
            file=sys.stderr,
        )
        return 1

    print(f"[serve] starting agent-memory-lite on http://127.0.0.1:{PORT}")
    print("[serve] Ctrl+C to stop")
    return subprocess.call(
        [sys.executable, "-m", "agent_memory_lite"],
        cwd=str(REPO_ROOT),
    )


if __name__ == "__main__":
    sys.exit(main())
