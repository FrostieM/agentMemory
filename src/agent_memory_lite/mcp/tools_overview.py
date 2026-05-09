"""2.0: MCP tool — workspace code overview.

Mirrors GET /memory/code_overview so an agent can ask "give me the
dashboard payload" without going through HTTP. Re-uses the same
SQL helpers as the route handler so output is byte-identical.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_memory_lite.api.routes.code_overview_db import (
    gather_counts,
    gather_top_called,
)
from agent_memory_lite.repositories.active_edits_repo import (
    cleanup_expired,
    list_active_claims,
)
from agent_memory_lite.repositories.file_digests_repo import list_digests
from agent_memory_lite.repositories.symbol_versions_breaking import (
    list_recent_signature_changes,
)


def memory_code_overview(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    breaking_days: int = 7,
    files_limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    cleanup_expired(conn, workspace_id=workspace_id)
    counts = gather_counts(conn, workspace_id)
    digests = list_digests(conn, workspace_id=workspace_id, limit=files_limit)
    cutoff = (datetime.now(UTC) - timedelta(days=breaking_days)).isoformat()
    pairs = list_recent_signature_changes(
        conn, workspace_id=workspace_id, since_iso=cutoff, limit=20
    )
    edits = list_active_claims(conn, workspace_id=workspace_id, limit=50)
    return {
        "workspace_id": workspace_id,
        "counts": counts.model_dump(),
        "recent_files": [
            {
                "file_path": d.file_path,
                "language": d.language,
                "symbol_count": d.symbol_count,
                "inbound_edge_count": d.inbound_edge_count,
                "outbound_edge_count": d.outbound_edge_count,
                "versions_recent": d.versions_recent,
                "narrative": d.narrative,
                "updated_at": d.updated_at,
            }
            for d in digests
        ],
        "breaking": [
            {
                "qualified_name": cur.qualified_name,
                "file_path": cur.file_path,
                "prev_signature": prev.signature_text,
                "new_signature": cur.signature_text,
                "new_at": cur.created_at,
            }
            for prev, cur in pairs
        ],
        "active_edits": [
            {
                "qualified_name": e.qualified_name,
                "file_path": e.file_path,
                "agent_id": e.agent_id,
                "expires_at": e.expires_at,
                "note": e.note,
            }
            for e in edits
        ],
        "top_called": [h.model_dump() for h in gather_top_called(conn, workspace_id, 15)],
    }
