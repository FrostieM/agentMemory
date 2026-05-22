"""Hub-mode workspace -> physical-path resolution helper.

In hub mode the same HTTP service serves multiple registered workspaces.
The MCP stdio server already routes per call by injecting
``X-Memory-DB-Path`` / ``X-Memory-Vector-Path`` headers, so any in-process
agent automatically reaches the right SQLite + LanceDB pair. Direct HTTP
clients (``curl``, ad-hoc ``httpx`` calls, the local UI) historically had
no such routing: a request like
``GET /memory/hygiene_report?workspace_id=copyBot`` would silently hit
the service's anchor DB and report ``status=ok`` because ``copyBot``'s
rows were never seen.

This module is the small pure resolver shared by the routing middleware
(see ``workspace_routing_middleware.py``) and any future caller that
needs to translate a ``workspace_id`` into the registered DB / vector
paths. It is intentionally separate from the middleware so unit tests
can exercise the resolution logic without ASGI plumbing.

It also hosts ``ensure_workspace_matches_db`` -- the write guard that
fails a write loudly when the connection landed on the wrong
workspace's DB, instead of letting it silently corrupt.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agent_memory_lite.api.errors import ValidationError
from agent_memory_lite.config.settings import Settings
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


def _connection_db_path(conn: sqlite3.Connection) -> str:
    """Absolute file backing the connection's ``main`` schema.

    ``PRAGMA database_list`` reports the physical file SQLite opened.
    Empty for an in-memory or temp database -- the guard then has no
    path to compare against and skips.
    """
    try:
        for row in conn.execute("PRAGMA database_list"):
            if row[1] == "main":
                return str(row[2] or "")
    except sqlite3.Error:
        return ""
    return ""


def _expected_db_path(workspace_id: str, settings: Settings) -> str | None:
    """Registry DB path ``workspace_id`` is supposed to live in.

    ``None`` when there is nothing authoritative to compare against --
    an unregistered workspace that is also not the service's anchor.
    The guard skips rather than guess in that case.
    """
    resolved = resolve_workspace_paths(workspace_id, settings)
    if resolved is not None:
        return resolved.db_path
    if workspace_id and workspace_id == settings.workspace_id:
        return str(settings.db_path)
    return None


def _connections_match(actual: str, expected: str) -> bool:
    """True when ``actual`` and ``expected`` name the same physical file
    -- or cannot be told apart.

    ``os.path.samefile`` compares device + inode, so it sees through
    symlinks, ``subst`` drives and UNC-vs-drive-letter forms that a
    ``Path.resolve()`` string compare can miss. It needs both files to
    exist; when one does not, fall back to a resolved-path compare. When
    even that fails, treat the pair as matching -- a transient
    resolution glitch must never false-reject a legitimate write.
    """
    try:
        return os.path.samefile(actual, expected)
    except OSError:
        pass
    try:
        return Path(actual).resolve() == Path(expected).resolve()
    except (OSError, ValueError):
        return True


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
    ``workspace_id`` (see ``_connections_match``) and raises before any
    row is written when they are different files.

    No-op when the expected DB cannot be determined: an unregistered
    workspace, or one the registry co-locates in the anchor DB, or an
    in-memory connection with no physical file. Registry *integrity*
    (a workspace pointed at the wrong DB) is ``memory_audit``'s domain,
    not this routing guard's.
    """
    expected = _expected_db_path(workspace_id, settings)
    if expected is None:
        return
    actual = _connection_db_path(conn)
    if not actual:
        return
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
