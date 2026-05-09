"""1.8.0: MCP tools — file digest lookup."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.repositories.file_digests_repo import get_digest, list_digests


def memory_file_digest(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    file_path: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    digest = get_digest(conn, workspace_id=workspace_id, file_path=file_path)
    if digest is None:
        return {
            "workspace_id": workspace_id,
            "file_path": file_path,
            "found": False,
            "digest": None,
        }
    return {
        "workspace_id": workspace_id,
        "file_path": file_path,
        "found": True,
        "digest": digest.model_dump(),
    }


def memory_list_file_digests(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    limit: int = 50,
    **_kwargs: Any,
) -> dict[str, Any]:
    rows = list_digests(conn, workspace_id=workspace_id, limit=limit)
    return {
        "workspace_id": workspace_id,
        "total": len(rows),
        "digests": [d.model_dump() for d in rows],
    }
