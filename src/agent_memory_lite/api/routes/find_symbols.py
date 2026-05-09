"""POST /memory/find_symbols — exact symbol-level lookup across code chunks.

1.4.0: completes the symbol-level loop — once tree-sitter dispatch
populates ``chunks.qualified_name`` / ``chunks.symbol_kind``, an agent
that knows the symbol name (``paperBot.calculate``, ``Selector::admit``,
``RouteHandler``) can land directly on the chunk body without paying
for FTS / vector retrieval.

Read-only; never mutates. Pre-1.4.0 chunks (qualified_name IS NULL)
are invisible to this endpoint by design — they live in the legacy
text-window slice and the agent should fall back to ``/memory/search``
for those.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable

router = APIRouter()

# Allowed values per migration 0028 comment. Validated client-side so
# a typo doesn't silently return zero rows.
_ALLOWED_KINDS: frozenset[str] = frozenset(
    {"function", "class", "method", "struct", "interface", "enum", "type"}
)


class FindSymbolsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    name: str | None = Field(
        default=None,
        max_length=400,
        description="Exact qualified name match. Case-sensitive.",
    )
    name_prefix: str | None = Field(
        default=None,
        max_length=400,
        description=(
            "Prefix match against qualified_name. Useful for "
            "'paperBot.' to list every method on a class."
        ),
    )
    kinds: list[str] = Field(
        default_factory=list,
        description=(
            "Filter by symbol_kind. Empty list means any kind. Allowed "
            "values: function, class, method, struct, interface, enum, type."
        ),
    )
    languages: list[str] = Field(
        default_factory=list,
        description="Filter by chunks.metadata_json.language. Empty means any.",
    )
    limit: int = Field(default=20, ge=1, le=200)


class FindSymbolHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: str
    qualified_name: str
    symbol_kind: str | None
    parent_qualified_name: str | None
    language: str | None
    path: str | None
    line_start: int | None
    line_end: int | None
    text: str


class FindSymbolsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    total: int
    hits: list[FindSymbolHit]


@router.post("/memory/find_symbols", response_model=FindSymbolsResponse)
def find_symbols_route(
    payload: FindSymbolsRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> FindSymbolsResponse:
    ensure_workspace_readable(payload.workspace_id, settings)

    bad_kinds = [k for k in payload.kinds if k not in _ALLOWED_KINDS]
    if bad_kinds:
        raise HTTPException(
            status_code=400,
            detail=(f"Unknown symbol kinds: {bad_kinds!r}. Allowed: {sorted(_ALLOWED_KINDS)}"),
        )

    sql_parts: list[str] = [
        "SELECT id, qualified_name, symbol_kind, parent_qualified_name, "
        "line_start, line_end, text, metadata_json "
        "FROM chunks WHERE workspace_id = ? AND qualified_name IS NOT NULL"
    ]
    params: list[object] = [payload.workspace_id]

    if payload.name is not None:
        sql_parts.append(" AND qualified_name = ?")
        params.append(payload.name)
    if payload.name_prefix is not None:
        sql_parts.append(" AND qualified_name LIKE ?")
        params.append(f"{payload.name_prefix}%")
    if payload.kinds:
        placeholders = ", ".join("?" * len(payload.kinds))
        sql_parts.append(f" AND symbol_kind IN ({placeholders})")
        params.extend(payload.kinds)

    sql_parts.append(" ORDER BY qualified_name ASC, line_start ASC LIMIT ?")
    params.append(payload.limit)

    rows = conn.execute("".join(sql_parts), params).fetchall()
    hits = [_row_to_hit(row) for row in rows]
    if payload.languages:
        wanted = {lang.lower() for lang in payload.languages}
        hits = [h for h in hits if (h.language or "").lower() in wanted]

    return FindSymbolsResponse(
        workspace_id=payload.workspace_id,
        total=len(hits),
        hits=hits,
    )


def _row_to_hit(row: sqlite3.Row) -> FindSymbolHit:
    metadata = json.loads(row["metadata_json"] or "{}")
    return FindSymbolHit(
        chunk_id=str(row["id"]),
        qualified_name=str(row["qualified_name"]),
        symbol_kind=(str(row["symbol_kind"]) if row["symbol_kind"] else None),
        parent_qualified_name=(
            str(row["parent_qualified_name"]) if row["parent_qualified_name"] else None
        ),
        language=metadata.get("language"),
        path=metadata.get("path"),
        line_start=row["line_start"],
        line_end=row["line_end"],
        text=str(row["text"]),
    )
