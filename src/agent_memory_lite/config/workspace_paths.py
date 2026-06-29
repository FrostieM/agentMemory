"""Workspace-id → physical-path resolution + connection/workspace guard.

In hub mode one HTTP service (or one MCP process) serves several
registered workspaces, each with its own SQLite + LanceDB pair. Two
callers need to translate a ``workspace_id`` into the registered paths
and to verify a live connection actually landed on that workspace's DB:

* the API routing middleware (``api/workspace_routing_middleware.py``),
* the trigger-on-traffic sentinel / brain pass (``maintenance/``).

This resolver lives in ``config`` -- the lowest shared layer -- so both
callers reach the SAME logic instead of ``maintenance`` importing across
the ``maintenance -> api`` layer boundary. ``api/workspace_routing.py``
keeps the strict write guard that rejects unregistered workspaces and
turns a guard failure into the API-layer ``ValidationError``. The sentinel
scheduler calls the softer ``connection_matches_workspace`` predicate
directly and skips only on definite DB mismatches.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.config.settings import Settings

# Re-exported (back-compat) -- the low-level physical-path helpers moved to a
# sibling module to keep this file at/under the 150-SLOC ceiling.
from agent_memory_lite.config.workspace_connection_match import (
    _connection_db_path,
    _connections_match,
)
from agent_memory_lite.config.workspace_registry import WorkspaceRegistry


@dataclass(frozen=True)
class ResolvedWorkspacePaths:
    """Result of resolving a workspace_id through the hub registry."""

    db_path: str
    vector_path: str


def resolve_workspace_paths(
    workspace_id: str | None, settings: Settings
) -> ResolvedWorkspacePaths | None:
    """Return registered ``(db_path, vector_path)`` for ``workspace_id``.

    Returns ``None`` when:

    * ``workspace_id`` is empty,
    * the registry file does not exist or is unreadable,
    * the workspace is not registered, or
    * the registered ``db_path`` matches the service's anchor (no routing
      needed — the request already lands on the right DB).
    """
    if not workspace_id:
        return None
    try:
        registry = WorkspaceRegistry(settings.workspaces_file)
        entry = registry.get(workspace_id)
    except Exception:
        return None
    if entry is None or not entry.db_path:
        return None
    if str(entry.db_path) == str(settings.db_path):
        return None
    vector_path = entry.vector_path or str(settings.vector_db_path)
    return ResolvedWorkspacePaths(db_path=entry.db_path, vector_path=vector_path)


def registry_load_error(settings: Settings) -> str | None:
    """Return the workspace registry's ``last_load_error`` (corrupt-vs-absent).

    ``'corrupt:json'`` / ``'corrupt:io'`` / ``'corrupt:shape'`` when the registry
    file exists but cannot be read, else ``None`` (absent or parsed cleanly). Lets
    routing guards distinguish "registry unavailable" (a transient/operator issue --
    surface it, fail-closed) from "workspace genuinely not registered". Failure-soft:
    any unexpected error reading the registry maps to a generic corrupt marker.
    """
    try:
        registry = WorkspaceRegistry(settings.workspaces_file)
        registry.list()  # triggers _load(), which sets last_load_error
        return registry.last_load_error
    except Exception:  # pragma: no cover - defensive
        return "corrupt:unknown"


def workspace_db_path(workspace_id: str | None, settings: Settings) -> str | None:
    """Return the authoritative DB path for a writable workspace.

    The anchor workspace is authoritative even when it is not listed in the
    registry. Foreign workspaces must be registered; otherwise callers must not
    silently fall back to the anchor DB for writes.
    """
    if not workspace_id:
        return None
    if workspace_id == settings.workspace_id:
        return str(settings.db_path)
    try:
        registry = WorkspaceRegistry(settings.workspaces_file)
        entry = registry.get(workspace_id)
    except Exception:
        return None
    if entry is None or not entry.db_path:
        return None
    return entry.db_path


def workspace_vector_path(workspace_id: str | None, settings: Settings) -> str | None:
    """Return the authoritative LanceDB path for a writable workspace.

    The vector-store mirror of ``workspace_db_path``: the anchor is
    authoritative even when unregistered; a foreign workspace must be registered
    (we key on ``db_path`` so the pair stays consistent with the SQL guard) and
    its ``vector_path`` falls back to the anchor's only when the registry leaves
    it blank. ``None`` for an unregistered, non-anchor workspace -- callers must
    not guess a vector store for it.
    """
    if not workspace_id:
        return None
    if workspace_id == settings.workspace_id:
        return str(settings.vector_db_path)
    try:
        registry = WorkspaceRegistry(settings.workspaces_file)
        entry = registry.get(workspace_id)
    except Exception:
        return None
    if entry is None or not entry.db_path:
        return None
    return entry.vector_path or str(settings.vector_db_path)


def _expected_db_path(workspace_id: str, settings: Settings) -> str | None:
    """Registry DB path ``workspace_id`` is supposed to live in.

    ``None`` when there is nothing authoritative to compare against --
    an unregistered workspace that is also not the service's anchor.
    The guard skips rather than guess in that case.
    """
    return workspace_db_path(workspace_id, settings)


def connection_matches_workspace(
    conn: sqlite3.Connection, workspace_id: str, settings: Settings
) -> bool:
    """True when ``conn``'s physical DB is ``workspace_id``'s registered DB.

    This is intentionally a soft sentinel predicate, not a write guard.
    It returns True ("skip -- do not guess") when there is nothing
    authoritative to compare against: an unregistered non-anchor
    workspace, or an in-memory connection with no physical file. It
    returns ``False`` ONLY on a definite mismatch -- the connection
    landed on a different file than the registry records for this
    workspace.

    Mutation paths must use ``workspace_db_path`` / the API or MCP write
    guards so unregistered workspace IDs cannot fall through into the
    anchor DB.
    """
    expected = _expected_db_path(workspace_id, settings)
    if expected is None:
        return True
    actual = _connection_db_path(conn)
    if not actual:
        return True
    return _connections_match(actual, expected)
