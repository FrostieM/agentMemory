"""1.7.0: MCP tools for multi-agent coordination — active-edit
registry + soft-graph neighbors.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.models.active_edits import ActiveEditIn
from agent_memory_lite.repositories.active_edits_repo import (
    cleanup_expired,
    delete_claim,
    find_active_claim,
    insert_claim,
    list_active_claims,
)
from agent_memory_lite.repositories.soft_edges_repo import list_soft_neighbors


def memory_claim_edit(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    agent_id: str,
    qualified_name: str | None = None,
    file_path: str | None = None,
    ttl_minutes: int = 30,
    note: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    if qualified_name is None and file_path is None:
        raise ValueError("claim_edit requires either qualified_name or file_path")
    cleanup_expired(conn, workspace_id=workspace_id)
    existing = find_active_claim(
        conn,
        workspace_id=workspace_id,
        qualified_name=qualified_name,
        file_path=file_path,
    )
    if existing is not None and existing.agent_id != agent_id:
        return {
            "claimed": False,
            "blocked_by": existing.agent_id,
            "blocked_until": existing.expires_at,
            "claim_id": existing.id,
        }
    if existing is not None:
        delete_claim(conn, claim_id=existing.id)
    edit = insert_claim(
        conn,
        ActiveEditIn(
            workspace_id=workspace_id,
            agent_id=agent_id,
            qualified_name=qualified_name,
            file_path=file_path,
            ttl_minutes=ttl_minutes,
            note=note,
        ),
    )
    return {"claimed": True, "claim_id": edit.id, "expires_at": edit.expires_at}


def memory_release_edit(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    claim_id: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    removed = delete_claim(conn, claim_id=claim_id)
    return {"workspace_id": workspace_id, "released": bool(removed)}


def memory_list_active_edits(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    limit: int = 100,
    **_kwargs: Any,
) -> dict[str, Any]:
    cleanup_expired(conn, workspace_id=workspace_id)
    edits = list_active_claims(conn, workspace_id=workspace_id, limit=limit)
    return {
        "workspace_id": workspace_id,
        "total": len(edits),
        "edits": [e.model_dump() for e in edits],
    }


def memory_soft_neighbors(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    src_qualified_name: str,
    edge_kinds: list[str] | None = None,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    rows = list_soft_neighbors(
        conn,
        workspace_id=workspace_id,
        src_qualified_name=src_qualified_name,
        edge_kinds=edge_kinds or None,
        limit=limit,
    )
    return {
        "workspace_id": workspace_id,
        "src_qualified_name": src_qualified_name,
        "total": len(rows),
        "neighbors": [r.model_dump() for r in rows],
    }
