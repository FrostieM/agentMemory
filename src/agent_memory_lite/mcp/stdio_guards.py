"""Workspace-isolation guards used by every MCP handler.

Reads to any registered workspace are allowed; writes outside the
anchor workspace are blocked under
``MEMORY_STRICT_WORKSPACE_ISOLATION`` (asymmetric isolation).
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.stdio_runtime import _runtime


def _drop_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _ensure_workspace_readable(workspace_id: str) -> None:
    """Read guard: only enforces forbid_default. Reads to any registered
    workspace are allowed (the user can explicitly ask the agent to look)."""
    if _runtime.settings.forbid_default_workspace and workspace_id == "default":
        raise ValueError("workspace_id='default' is disabled by MEMORY_FORBID_DEFAULT_WORKSPACE")


def _ensure_workspace_writable(workspace_id: str) -> None:
    """Write guard: blocks writes to any non-anchor workspace under strict
    isolation. Hub mode opts out (operator chose a shared service)."""
    _ensure_workspace_readable(workspace_id)
    if (
        _runtime.settings.strict_workspace_isolation
        and not _runtime.settings.hub_mode
        and workspace_id != _runtime.settings.workspace_id
    ):
        raise ValueError(
            f"writes to workspace_id={workspace_id!r} are blocked by "
            f"MEMORY_STRICT_WORKSPACE_ISOLATION; expected "
            f"{_runtime.settings.workspace_id!r}. Reads remain allowed."
        )


# Backwards-compatible alias for the write guard.
def _ensure_workspace_allowed(workspace_id: str) -> None:
    _ensure_workspace_writable(workspace_id)


def _with_workspace(payload: dict[str, Any], *, intent: str = "write") -> dict[str, Any]:
    """Normalize MCP tool payload + apply read or write guard.

    ``intent="read"`` lets the caller fetch any registered workspace; the user
    asked explicitly. ``intent="write"`` (default) enforces strict isolation
    so a project chat cannot write to another workspace by accident.
    """
    cleaned = _drop_none(payload)
    cleaned.setdefault("workspace_id", _runtime.settings.workspace_id)
    workspace_id = str(cleaned["workspace_id"])
    if intent == "read":
        _ensure_workspace_readable(workspace_id)
    else:
        _ensure_workspace_writable(workspace_id)
    return cleaned


def _workspace_from_args(args: dict[str, Any], *, intent: str = "write") -> str:
    workspace_id = str(args.get("workspace_id", _runtime.settings.workspace_id))
    if intent == "read":
        _ensure_workspace_readable(workspace_id)
    else:
        _ensure_workspace_writable(workspace_id)
    return workspace_id
