"""1.4.0: MCP tool — symbol-level chunk lookup.

Mirrors POST /memory/find_symbols so MCP-only deployments can land on
a ``Class.method`` directly without paying for FTS / vector retrieval.
Returns rows whose ``qualified_name`` matches; pre-1.4.0 chunks
(qualified_name IS NULL) are invisible by design and the agent should
fall back to ``memory_search`` for those.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def memory_find_symbols(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    name: str | None = None,
    name_prefix: str | None = None,
    kinds: list[str] | None = None,
    languages: list[str] | None = None,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    sql_parts: list[str] = [
        "SELECT id, qualified_name, symbol_kind, parent_qualified_name, "
        "line_start, line_end, text, metadata_json "
        "FROM chunks WHERE workspace_id = ? AND qualified_name IS NOT NULL"
    ]
    params: list[object] = [workspace_id]
    if name is not None:
        sql_parts.append(" AND qualified_name = ?")
        params.append(name)
    if name_prefix is not None:
        sql_parts.append(" AND qualified_name LIKE ?")
        params.append(f"{name_prefix}%")
    if kinds:
        placeholders = ", ".join("?" * len(kinds))
        sql_parts.append(f" AND symbol_kind IN ({placeholders})")
        params.extend(kinds)
    sql_parts.append(" ORDER BY qualified_name ASC, line_start ASC LIMIT ?")
    params.append(limit)

    rows = conn.execute("".join(sql_parts), params).fetchall()
    hits: list[dict[str, Any]] = []
    for row in rows:
        meta = json.loads(row["metadata_json"] or "{}")
        if languages and (meta.get("language") or "").lower() not in {
            lang.lower() for lang in languages
        }:
            continue
        hits.append(
            {
                "chunk_id": row["id"],
                "qualified_name": row["qualified_name"],
                "symbol_kind": row["symbol_kind"],
                "parent_qualified_name": row["parent_qualified_name"],
                "language": meta.get("language"),
                "path": meta.get("path"),
                "line_start": row["line_start"],
                "line_end": row["line_end"],
                "text": row["text"],
            }
        )
    return {"workspace_id": workspace_id, "total": len(hits), "hits": hits}
