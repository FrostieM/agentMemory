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
"""

from __future__ import annotations

from dataclasses import dataclass

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
