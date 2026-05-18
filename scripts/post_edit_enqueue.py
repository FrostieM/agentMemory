"""PostToolUse hook: enqueue a digest refresh for the edited file.

Wired into Claude Code's PostToolUse hook for Edit / Write / NotebookEdit.
After each successful file mutation, this script drops a marker line
into the digest queue file; the ``digest_worker.py`` daemon consumes
the queue and refreshes ``code_digests`` rows.

Stdin: Claude Code's PostToolUse event JSON with at least

    {"tool_name": "Edit", "tool_input": {"file_path": "/abs/path"}, ...}

Stdout: nothing on success. Failure-soft: any error path is silent so
the agent's main turn is never disrupted by hook plumbing.

Configure in ``~/.claude/settings.json``::

    {
      "hooks": {
        "PostToolUse": [{
          "matcher": "Edit|Write|NotebookEdit",
          "hooks": [{
            "type": "command",
            "command": "<venv-python> <repo>/scripts/post_edit_enqueue.py"
          }]
        }]
      }
    }
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

REGISTRY_PATH = (
    Path(os.environ["MEMORY_WORKSPACES_FILE"])
    if os.environ.get("MEMORY_WORKSPACES_FILE")
    else Path.home() / ".agent_memory" / "workspaces.json"
)
DEFAULT_WORKSPACE = os.environ.get("AGENT_MEMORY_WORKSPACE", "")


def _read_event() -> dict[str, object]:
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _extract_file_path(event: dict[str, object]) -> str:
    """Pull the file_path out of an Edit / Write / NotebookEdit event."""
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return ""
    candidates = ("file_path", "notebook_path", "path")
    for key in candidates:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _list_registry() -> list[dict[str, object]]:
    if not REGISTRY_PATH.exists():
        return []
    try:
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    entries = payload.get("workspaces")
    return entries if isinstance(entries, list) else []


def _resolve_workspace(file_path: Path) -> tuple[str, str]:
    """Walk parents to find the workspace this file belongs to.

    Returns (workspace_id, db_path) — empty strings if no registered
    workspace covers this file.
    """
    candidate = file_path.resolve()
    parents = [candidate, *candidate.parents]
    entries = _list_registry()
    for parent in parents:
        target = str(parent).rstrip("\\/").casefold()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            root = str(entry.get("project_root", "")).rstrip("\\/").casefold()
            if root and target == root:
                return (str(entry.get("id", "")), str(entry.get("db_path", "")))
    return ("", "")


def main() -> int:
    event = _read_event()
    file_path = _extract_file_path(event)
    if not file_path:
        return 0
    try:
        src = Path(file_path)
        if not src.is_file():
            return 0
        workspace_id, db_path = _resolve_workspace(src)
        if not workspace_id or not db_path:
            return 0
        # Defer the heavy import until we have something to enqueue. The
        # queue I/O itself is cheap (one append per hook); we don't want
        # to add tens of ms for events that don't apply.
        from agent_memory_lite.cognition.digest_worker import enqueue  # noqa: PLC0415

        enqueue(workspace_id=workspace_id, db_path=db_path, file_path=str(src.resolve()))
    except Exception:  # hook must never raise into the agent
        with contextlib.suppress(Exception):
            sys.stderr.write("post_edit_enqueue: silent error path\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
