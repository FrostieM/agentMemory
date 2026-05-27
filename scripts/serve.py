"""Start the agent-memory-lite HTTP service.

Single-command launcher for the FastAPI service that backs the
UserPromptSubmit hook and any non-MCP client. Runs in the foreground —
Ctrl+C to stop.

    python scripts/serve.py            # auto: hub mode if registry has entries
    python scripts/serve.py --strict   # force single-workspace mode
    python scripts/serve.py --hub      # force hub mode

What it does, in order:
1. Bootstraps `<repo>/.agent_memory/memory.db` if missing.
2. Refuses to start if port 8765 is already in use.
3. Decides hub vs strict mode (CLI flag > env override > registry presence).
4. Calls `python -m agent_memory_lite` with the right env.

Hub mode is the default whenever `~/.agent_memory/workspaces.json` lists at
least one project workspace. In hub mode the service routes per-request via
`X-Memory-DB-Path` headers, so multiple project hooks point at the same
service without 400s. In strict mode the service rejects any `workspace_id`
that does not match `MEMORY_WORKSPACE_ID` — only useful for a single-project
deployment.

Set `AGENT_MEMORY_PORT` to override the default 8765.
Set `MEMORY_HUB_MODE=false` to opt out even when the registry has entries.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("AGENT_MEMORY_PORT", "8765"))
DEFAULT_REGISTRY = Path.home() / ".agent_memory" / "workspaces.json"


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


def registry_has_entries(path: Path | None = None) -> bool:
    target = path if path is not None else DEFAULT_REGISTRY
    if not target.exists():
        return False
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(isinstance(payload, dict) and payload.get("workspaces"))


def decide_hub_mode(args: argparse.Namespace) -> bool:
    """Hub on/off precedence: CLI flag > env > registry presence."""
    if args.hub:
        return True
    if args.strict:
        return False
    env_value = os.environ.get("MEMORY_HUB_MODE")
    if env_value is not None:
        return env_value.strip().lower() in {"1", "true", "yes", "on"}
    return registry_has_entries()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the agent-memory-lite HTTP service.")
    parser.add_argument(
        "--hub",
        action="store_true",
        help="Force hub mode (multi-project routing via X-Memory-DB-Path).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Force strict single-workspace mode.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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

    hub_mode = decide_hub_mode(args)
    env = dict(os.environ)
    env["MEMORY_HUB_MODE"] = "true" if hub_mode else "false"
    print(f"[serve] starting agent-memory-lite on http://127.0.0.1:{PORT}")
    print(f"[serve] hub mode: {'ON' if hub_mode else 'OFF'} (registry={DEFAULT_REGISTRY})")
    if hub_mode:
        print(
            "[serve] hub mode accepts any workspace_id when the request "
            "supplies X-Memory-DB-Path. Use this for shared multi-project "
            "service."
        )
    else:
        print(
            "[serve] strict mode pins the service to MEMORY_WORKSPACE_ID. "
            "Only that workspace_id is accepted."
        )
    print("[serve] Ctrl+C to stop")
    return subprocess.call(
        [sys.executable, "-m", "agent_memory_lite"],
        cwd=str(REPO_ROOT),
        env=env,
    )


if __name__ == "__main__":
    sys.exit(main())
