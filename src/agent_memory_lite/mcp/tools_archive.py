"""In-process MCP handler for memory_archive."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.ingestion.archive_service import archive_memory_object


def memory_archive(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    archive_flag = payload.get("archive")
    archive = True if archive_flag is None else bool(archive_flag)
    return archive_memory_object(
        conn,
        workspace_id=str(payload.get("workspace_id", "default")),
        kind=str(payload["kind"]),
        object_id=str(payload["id"]),
        archive=archive,
    )
