"""API-layer workspace write guard.

The workspace-id → path resolver and the connection/workspace match
predicate moved to ``config/workspace_paths.py`` so the maintenance
sentinel can share them without importing across the
``maintenance -> api`` layer boundary. This module keeps the
API-facing piece -- ``ensure_workspace_matches_db`` raises the
``ValidationError`` the route layer maps to a 4xx -- and re-exports the
resolver names so existing importers (the routing middleware, the
ingest routes, their tests) are unaffected.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.errors import ValidationError
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.config.workspace_paths import (
    ResolvedWorkspacePaths,
    _connection_db_path,
    _expected_db_path,
    connection_matches_workspace,
    resolve_workspace_paths,
)

__all__ = [
    "ResolvedWorkspacePaths",
    "connection_matches_workspace",
    "ensure_workspace_matches_db",
    "resolve_workspace_paths",
]


def ensure_workspace_matches_db(
    conn: sqlite3.Connection, workspace_id: str, settings: Settings
) -> None:
    """Reject a write whose connection is not the workspace's own DB.

    ``WorkspaceRoutingMiddleware`` routes a hub-mode request to the DB
    registered for the body's ``workspace_id``. When that routing is
    bypassed -- a service started before the middleware existed, a wrong
    ``X-Memory-DB-Path`` header, or a service run with both ``hub_mode``
    and ``strict_workspace_isolation`` off -- the write silently lands in
    the anchor DB. On 2026-05-21, 134 copyBot ``ingest_file`` calls
    leaked into the agent-memory-lite database exactly this way.

    ``ensure_workspace_writable`` cannot catch it: in hub mode it permits
    every ``workspace_id`` by design, and with strict isolation off it
    permits foreign writes too. This guard is the backstop -- it checks
    the physical file behind ``conn`` against the registry path for
    ``workspace_id`` (see ``connection_matches_workspace``) and raises
    before any row is written when they are different files.

    No-op when the expected DB cannot be determined: an unregistered
    workspace, or one the registry co-locates in the anchor DB, or an
    in-memory connection with no physical file. Registry *integrity*
    (a workspace pointed at the wrong DB) is ``memory_audit``'s domain,
    not this routing guard's.
    """
    if connection_matches_workspace(conn, workspace_id, settings):
        return
    expected = _expected_db_path(workspace_id, settings)
    actual = _connection_db_path(conn)
    raise ValidationError(
        f"workspace_id={workspace_id!r} routed to the wrong database: the "
        f"write reached {actual!r} but this workspace's registered DB is "
        f"{expected!r}. The hub router did not send the call to the "
        "workspace's own DB -- restart the HTTP service so it picks up "
        "WorkspaceRoutingMiddleware, or pass a correct X-Memory-DB-Path "
        "header. The write was rejected to prevent cross-workspace pollution."
    )
