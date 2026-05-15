"""Claude Code PreToolUse hook: enforce active workspace rules.

Wired into Claude Code's hook system, this runs BEFORE every tool
invocation. It reads the tool name + payload from stdin, resolves the
workspace for ``cwd``, loads active enforcement-tagged behavior
instructions, and routes them through ``enforcement.dispatch.decide``.

Exit codes:

* ``0`` — allow the tool call.
* ``2`` — block the tool call; stderr is shown to the model so it can
  fix the violation and retry.

Failure-soft: any unexpected error (missing workspace, IO error,
import failure) returns exit 0 so a buggy hook never deadlocks the
agent. The trade-off is that a broken hook degrades to "no
enforcement" instead of "every tool call denied".

Configure in ``~/.claude/settings.json``:

    {
      "hooks": {
        "PreToolUse": [{
          "matcher": "Edit|Write|NotebookEdit|Bash|mcp__agent-memory-lite__memory_write_.*",
          "hooks": [{
            "type": "command",
            "command": "<venv-python> <repo>/scripts/pre_tool_use_check.py"
          }]
        }]
      }
    }

``scripts/setup_agent.py`` writes that snippet for you.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
with contextlib.suppress(AttributeError, ValueError):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


_BYPASS_FLAGS = {"1", "true", "yes", "on"}
_DEFAULT_REGISTRY = (
    Path(os.environ["MEMORY_WORKSPACES_FILE"])
    if os.environ.get("MEMORY_WORKSPACES_FILE")
    else Path.home() / ".agent_memory" / "workspaces.json"
)


def _bypass_enabled() -> bool:
    return os.environ.get("MEMORY_SKIP_PRETOOLUSE_CHECK", "").strip().lower() in _BYPASS_FLAGS


def _load_registry_entries() -> list[dict[str, Any]]:
    if not _DEFAULT_REGISTRY.exists():
        return []
    try:
        payload = json.loads(_DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload.get("workspaces") if isinstance(payload, dict) else None
    return entries if isinstance(entries, list) else []


def _resolve_workspace(cwd: str) -> tuple[str, str] | None:
    """Walk up from ``cwd`` until a registered project_root matches.

    Returns ``(workspace_id, db_path)`` or None if nothing matches. An
    explicit ``AGENT_MEMORY_WORKSPACE`` env var overrides the lookup
    when the registry holds a matching id.
    """
    entries = _load_registry_entries()
    if not entries:
        return None
    explicit = os.environ.get("AGENT_MEMORY_WORKSPACE", "").strip()
    if explicit:
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id") == explicit:
                db = str(entry.get("db_path", ""))
                if db:
                    return explicit, db
    target = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    for parent in [target, *target.parents]:
        target_str = str(parent).rstrip("\\/").casefold()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            root = str(entry.get("project_root", "")).rstrip("\\/").casefold()
            if root and root == target_str:
                wid = str(entry.get("id", ""))
                db = str(entry.get("db_path", ""))
                if wid and db:
                    return wid, db
    return None


def _read_event() -> dict[str, Any] | None:
    raw = sys.stdin.read()
    if not raw.strip():
        return None
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _decide_for_event(event: dict[str, Any]) -> tuple[bool, str]:  # noqa: PLR0911 - guard chain
    """Resolve workspace + run dispatch.decide; return (allow, diagnostic).

    Every failure path along the way collapses to ``(True, "")`` so the
    hook fails open. Diagnostic is only populated when a real block
    fires.
    """
    tool_name = event.get("tool_name")
    tool_input = event.get("tool_input") or {}
    transcript_path = event.get("transcript_path")
    cwd = event.get("cwd") or os.getcwd()
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return True, ""
    resolved = _resolve_workspace(cwd)
    if resolved is None:
        return True, ""
    workspace_id, db_path = resolved
    if not Path(db_path).exists():
        return True, ""
    try:
        from agent_memory_lite.enforcement.dispatch import decide  # noqa: PLC0415
        from agent_memory_lite.enforcement.session_trail import (  # noqa: PLC0415
            read_prior_tool_calls,
        )
    except ImportError:
        return True, ""
    trail = read_prior_tool_calls(transcript_path)
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error:
        return True, ""
    try:
        decision = decide(
            conn,
            workspace_id=workspace_id,
            tool_name=tool_name,
            tool_input=tool_input,
            trail=trail,
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL"),
            ollama_model=os.environ.get("OLLAMA_MODEL"),
        )
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return True, ""
    finally:
        conn.close()
    return decision.allow, decision.diagnostic


def main() -> int:
    if _bypass_enabled():
        return 0
    event = _read_event()
    if event is None:
        return 0
    allow_call, diagnostic = _decide_for_event(event)
    if allow_call:
        return 0
    print(diagnostic, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
