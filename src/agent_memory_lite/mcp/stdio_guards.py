"""Workspace-isolation guards used by every MCP handler.

Reads to any registered workspace are allowed; writes outside the
anchor workspace are blocked under
``MEMORY_STRICT_WORKSPACE_ISOLATION`` (asymmetric isolation).

Hub-mode bypass (Audit 1 #2): when ``hub_mode=True`` the write guard
intentionally allows cross-workspace writes (operator chose a shared
service). Pre-3.0.1 this happened SILENTLY — no signal in either DB
that the anchor process had reached into another workspace. The fix
logs a warning + an ``audit_log`` row into the anchor DB whenever a
cross-workspace write is permitted, so a misconfigured client doesn't
quietly mutate the wrong project's data. Opt-out via
``MEMORY_HUB_WRITE_AUDIT_ENABLED=false``.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from agent_memory_lite.config.workspace_paths import workspace_db_path
from agent_memory_lite.mcp.stdio_runtime import _runtime

_log = logging.getLogger(__name__)

# Per-process set tracking which foreign workspaces we've already warned
# about — keeps the log signal-to-noise high on long-lived stdio servers.
# Round-2 audit: HTTP and in-process MCP can race on the "membership
# check + add" pair from different threads; without the lock the warn-
# once dedup occasionally double-fires (audit row written twice).
_hub_write_warned: set[tuple[str, str]] = set()
_HUB_WRITE_WARNED_LOCK = threading.Lock()


def reset_hub_write_warned() -> None:
    """Test hook: clear the per-process hub-write warn-once set."""
    with _HUB_WRITE_WARNED_LOCK:
        _hub_write_warned.clear()


def _hub_write_audit_enabled() -> bool:
    raw = os.environ.get("MEMORY_HUB_WRITE_AUDIT_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _drop_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _ensure_workspace_readable(workspace_id: str) -> None:
    """Read guard: only enforces forbid_default. Reads to any registered
    workspace are allowed (the user can explicitly ask the agent to look)."""
    if _runtime.settings.forbid_default_workspace and workspace_id == "default":
        raise ValueError("workspace_id='default' is disabled by MEMORY_FORBID_DEFAULT_WORKSPACE")


def _audit_hub_cross_workspace_write(target_workspace_id: str) -> None:
    """In hub mode a write to ``target_workspace_id != anchor`` is allowed
    BUT not silent: warn-once per (anchor, target) + best-effort audit row
    into the anchor DB. Failure-soft — audit is informational, never blocks.
    """
    if not _hub_write_audit_enabled():
        return
    anchor = _runtime.settings.workspace_id
    if target_workspace_id == anchor:
        return
    key = (anchor, target_workspace_id)
    # Atomically check-and-add; otherwise two threads can both pass the
    # membership check and emit the audit row twice.
    with _HUB_WRITE_WARNED_LOCK:
        if key in _hub_write_warned:
            return
        _hub_write_warned.add(key)
    _log.warning(
        "hub-mode cross-workspace write: anchor=%r target=%r. Allowed by "
        "MEMORY_HUB_MODE=true; set MEMORY_STRICT_WORKSPACE_ISOLATION=true to "
        "block instead. This warning is emitted once per (anchor, target) pair.",
        anchor,
        target_workspace_id,
    )
    try:
        from agent_memory_lite.repositories.audit_repo import insert_audit  # noqa: PLC0415

        insert_audit(
            _runtime.db(),
            workspace_id=anchor,
            action="hub_cross_workspace_write",
            target_type="workspace",
            target_id=target_workspace_id,
            after={"anchor": anchor, "target": target_workspace_id},
        )
    except Exception:  # pragma: no cover - audit is informational
        pass


def _ensure_workspace_writable(workspace_id: str) -> None:
    """Write guard: blocks writes to any non-anchor workspace under strict
    isolation. Hub mode opts out (operator chose a shared service) but
    logs the cross-workspace write so it's never silent."""
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
    if workspace_db_path(workspace_id, _runtime.settings) is None:
        raise ValueError(
            f"workspace_id={workspace_id!r} is not registered and is not the "
            f"anchor workspace {_runtime.settings.workspace_id!r}. Register the "
            "workspace before writing."
        )
    # Hub-mode + cross-workspace: NOT blocked, but audited.
    if _runtime.settings.hub_mode and workspace_id != _runtime.settings.workspace_id:
        _audit_hub_cross_workspace_write(workspace_id)


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
