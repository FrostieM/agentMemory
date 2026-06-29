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

from agent_memory_lite.api.errors import ValidationError, WorkspaceUnavailableError
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.config.workspace_connection_match import (
    _connection_db_path,
    _connections_match,
)
from agent_memory_lite.config.workspace_paths import (
    ResolvedWorkspacePaths,
    connection_matches_workspace,
    registry_load_error,
    resolve_workspace_paths,
    workspace_db_path,
    workspace_vector_path,
)
from agent_memory_lite.config.workspace_read_guard import read_workspace_unavailable_reason

__all__ = [
    "ResolvedWorkspacePaths",
    "connection_matches_workspace",
    "ensure_store_matches_workspace",
    "ensure_workspace_matches_db",
    "ensure_workspace_readable_db",
    "resolve_workspace_paths",
    "workspace_db_path",
    "workspace_vector_path",
]


def ensure_workspace_readable_db(
    conn: sqlite3.Connection, workspace_id: str, settings: Settings
) -> None:
    """Refuse a READ whose connection is a DEFINITE mismatch for ``workspace_id``.

    The read-path analogue of ``ensure_workspace_matches_db``, but deliberately
    SOFT: reads to any registered workspace are allowed (strict isolation does
    not block reads), so this only raises on a DEFINITE physical-file mismatch --
    ``connection_matches_workspace`` returns ``False`` -- and stays a no-op for
    every ambiguous case it returns ``True`` for (unregistered non-anchor
    workspace, in-memory/temp connection, or a resolution glitch). That closes
    the read leak where a hub request for workspace B that was NOT routed to B's
    own DB would otherwise read B-tagged rows straight out of the anchor (A's)
    DB -- the read analogue of the 2026-05-21 pollution incident -- surfacing a
    misleading empty or, against a polluted anchor, another workspace's rows. Stays
    soft on a corrupt registry (unlike the write guard) -- see
    ``read_workspace_unavailable_reason`` for the deliberate read/write asymmetry.
    """
    reason = read_workspace_unavailable_reason(conn, workspace_id, settings)
    if reason is None:
        return
    raise WorkspaceUnavailableError(
        f"workspace_id={workspace_id!r} is unavailable on this connection: {reason}. "
        "The hub router did not route the read to the workspace's own DB -- restart "
        "the HTTP service so it picks up WorkspaceRoutingMiddleware, or pass a "
        "correct X-Memory-DB-Path header. The read was refused to avoid surfacing "
        "another workspace's rows."
    )


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

    No-op only for an in-memory connection with no physical file.
    Unregistered non-anchor workspaces are rejected: allowing them to
    fall through would write arbitrary workspace_id rows into the anchor
    or an explicitly routed DB.
    """
    actual = _connection_db_path(conn)
    if not actual:
        return
    expected = workspace_db_path(workspace_id, settings)
    if expected is None:
        # Distinguish a CORRUPT registry (cannot verify routing -> the workspace
        # may well be registered) from a genuinely-unregistered workspace, so the
        # operator gets an actionable message instead of a misleading "not
        # registered" for a registry that simply could not be read.
        load_error = registry_load_error(settings)
        if load_error is not None:
            raise ValidationError(
                f"workspace_id={workspace_id!r}: the workspace registry is "
                f"unavailable ({load_error}) -- cannot verify routing. Repair or "
                "restore the registry file before writing to prevent "
                "cross-workspace pollution."
            )
        raise ValidationError(
            f"workspace_id={workspace_id!r} is not registered and is not the "
            f"anchor workspace {settings.workspace_id!r}. Register the workspace "
            "before writing to prevent cross-workspace pollution."
        )
    if _connections_match(actual, expected):
        return
    raise ValidationError(
        f"workspace_id={workspace_id!r} routed to the wrong database: the "
        f"write reached {actual!r} but this workspace's registered DB is "
        f"{expected!r}. The hub router did not send the call to the "
        "workspace's own DB -- restart the HTTP service so it picks up "
        "WorkspaceRoutingMiddleware, or pass a correct X-Memory-DB-Path "
        "header. The write was rejected to prevent cross-workspace pollution."
    )


def ensure_store_matches_workspace(store: object, workspace_id: str, settings: Settings) -> None:
    """Reject an episode vector write whose store is not the workspace's .lance.

    The LanceDB analogue of ``ensure_workspace_matches_db``. ``store_for`` routes
    a vector store on a path SEPARATE from the SQL connection, so a routing bug
    could embed episode vectors into another workspace's store while the SQL row
    lands correctly (a half-leak the SQL guard cannot see). This backstop checks
    the store's physical ``.lance`` directory against the registry's vector path
    for ``workspace_id`` and raises before any vector is written when they
    differ.

    No-op when the store exposes no physical path (in-memory / test doubles).
    Unregistered non-anchor workspaces are rejected -- the same fail-closed
    stance as the SQL guard.
    """
    actual = getattr(store, "_db_path", None)
    if actual is None:
        return
    expected = workspace_vector_path(workspace_id, settings)
    if expected is None:
        raise ValidationError(
            f"workspace_id={workspace_id!r} is not registered and is not the "
            f"anchor workspace {settings.workspace_id!r}; refusing to route its "
            "episode vectors to prevent cross-workspace pollution."
        )
    if _connections_match(str(actual), expected):
        return
    raise ValidationError(
        f"workspace_id={workspace_id!r} episode vectors routed to the wrong "
        "vector store: the hub did not send them to the workspace's own .lance "
        "directory. The write was rejected to prevent cross-workspace pollution."
    )
