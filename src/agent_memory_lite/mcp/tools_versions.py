"""1.6.0: MCP tools — symbol-version history + breaking-change detection."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_memory_lite.repositories.symbol_edges_repo import list_edges_to
from agent_memory_lite.repositories.symbol_versions_breaking import (
    list_recent_signature_changes,
)
from agent_memory_lite.repositories.symbol_versions_repo import list_versions_for_qname


def memory_symbol_history(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    qualified_name: str,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    rows = list_versions_for_qname(
        conn,
        workspace_id=workspace_id,
        qualified_name=qualified_name,
        limit=limit,
    )
    return {
        "workspace_id": workspace_id,
        "qualified_name": qualified_name,
        "total": len(rows),
        "versions": [
            {
                "version_id": v.id,
                "qualified_name": v.qualified_name,
                "file_path": v.file_path,
                "chunk_id": v.chunk_id,
                "language": v.language,
                "signature_text": v.signature_text,
                "signature_hash": v.signature_hash,
                "content_hash": v.content_hash,
                "created_at": v.created_at,
            }
            for v in rows
        ],
    }


def memory_breaking_changes(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    since_days: int = 7,
    limit: int = 50,
    include_callers: bool = True,
    **_kwargs: Any,
) -> dict[str, Any]:
    cutoff = (datetime.now(UTC) - timedelta(days=since_days)).isoformat()
    pairs = list_recent_signature_changes(
        conn,
        workspace_id=workspace_id,
        since_iso=cutoff,
        limit=limit,
    )
    changes: list[dict[str, Any]] = []
    for prev, cur in pairs:
        caller_count = 0
        if include_callers:
            caller_count = len(
                list_edges_to(
                    conn,
                    workspace_id=workspace_id,
                    dst_qualified_name=cur.qualified_name,
                    edge_types=["calls", "instantiates"],
                    limit=500,
                )
            )
        changes.append(
            {
                "qualified_name": cur.qualified_name,
                "file_path": cur.file_path,
                "language": cur.language,
                "prev_signature": prev.signature_text,
                "new_signature": cur.signature_text,
                "prev_at": prev.created_at,
                "new_at": cur.created_at,
                "caller_count": caller_count,
            }
        )
    return {
        "workspace_id": workspace_id,
        "since_days": since_days,
        "total": len(changes),
        "changes": changes,
    }
