"""v3 reader API — memory_view / memory_get / memory_search.

These are the read-side primitives all agent surfaces (MCP, HTTP, CLI)
delegate to. They share three invariants:

1. **Compact projections by default.** Every result row goes through
   ``projections.project()`` before return. ~20-40 tokens per item.
2. **Selective field fetch.** ``memory_get(id, fields=[...])`` returns
   exactly the requested columns (plus id/kind). Default = projection.
3. **Read-only.** No mutations. Writer module owns those.

This module knows the v3 schema but does NOT depend on FastAPI, MCP,
or any agent runtime. It's pure SQL + dict transforms.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from agent_memory_lite.storage.projections import project

# ============================================================
# Result types
# ============================================================


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One hit from memory_search — compact projection + score."""

    kind: str
    projection: dict[str, Any]
    score: float
    snippet: str | None = None


# Kind → (table, id column) mapping. Used by both memory_get and
# memory_search to resolve an opaque kind string to its SQL location.
_KIND_TABLES = {
    "decision": ("decisions", "id"),
    "theory": ("theories", "id"),
    "behavior": ("behaviors", "id"),
    "skill": ("skills", "id"),
    "episode": ("episodes", "id"),
    "concept": ("concepts", "id"),
    "task": ("tasks", "id"),
    "insight": ("insights", "id"),
    "code_digest": ("code_digests", "id"),
    "chunk": ("chunks", "id"),
}


# Kind → list of free-text columns to BM25-search across.
_KIND_FTS_COLUMNS = {
    "decision": ["title", "decision_text", "rationale", "gist"],
    "theory": ["title", "claim", "mechanism", "gist"],
    "behavior": ["name", "rule", "rationale", "rule_one_line"],
    "skill": ["name", "summary", "when_to_use_short"],
    "episode": ["raw_text", "summary", "gist"],
    "concept": ["name", "definition", "definition_one_line"],
    "task": ["task_id", "goal", "goal_one_line", "next_action"],
    "insight": ["summary", "gist", "proposed_action"],
    "code_digest": ["file_path", "purpose_short", "narrative"],
}


# ============================================================
# memory_view — directory listing / single-row fetch
# ============================================================


def list_kind(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    limit: int = 20,
    pinned_only: bool = False,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List rows of ``kind`` as compact projections.

    Equivalent to the v2 ``_index.md`` per-kind listing concept, but
    cheap and indexed.
    """
    if kind not in _KIND_TABLES:
        return []
    table, _ = _KIND_TABLES[kind]
    where = ["workspace_id = ?"]
    params: list[Any] = [workspace_id]
    if pinned_only and kind in ("decision", "behavior"):
        where.append("pinned = 1")
    if status:
        where.append("status = ?")
        params.append(status)
    sql = f"SELECT * FROM {table} WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        projection = project(kind, row)
        if projection is not None:
            out.append(projection)
    return out


# ============================================================
# memory_get — fetch by id with selective fields
# ============================================================


def get_object(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    object_id: str,
    fields: list[str] | None = None,
) -> dict[str, Any] | None:
    """Fetch one row by id. Returns compact projection by default.

    Pass ``fields=['rationale','decision_text']`` to opt into full
    content for those columns. Caller controls token cost.
    """
    if kind not in _KIND_TABLES:
        return None
    table, id_col = _KIND_TABLES[kind]
    row = conn.execute(
        f"SELECT * FROM {table} WHERE workspace_id = ? AND {id_col} = ?",
        (workspace_id, object_id),
    ).fetchone()
    if row is None:
        return None
    base = project(kind, row) or {"id": object_id, "kind": kind}
    if not fields:
        return base
    extras: dict[str, Any] = {}
    for field in fields:
        try:
            extras[field] = row[field]
        except (KeyError, IndexError):
            continue
    return {**base, **extras}


# ============================================================
# memory_search — hybrid BM25 over FTS5 + per-kind fallback
# ============================================================


def search_kind(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    query: str,
    limit: int = 10,
) -> list[SearchHit]:
    """LIKE-based search across the kind's free-text columns.

    Pragmatic v1 implementation: scans `_KIND_FTS_COLUMNS[kind]` with
    LIKE patterns. The v3 plan adds FTS5 + LanceDB + jina rerank to
    the chunks table; this function handles non-chunk kinds where the
    body lives in the kind's own table.
    """
    if kind not in _KIND_TABLES or kind not in _KIND_FTS_COLUMNS:
        return []
    table, _ = _KIND_TABLES[kind]
    cols = _KIND_FTS_COLUMNS[kind]
    pattern = f"%{query.lower()}%"
    or_clauses = " OR ".join([f"LOWER(IFNULL({c}, '')) LIKE ?" for c in cols])
    sql = f"SELECT * FROM {table} WHERE workspace_id = ? AND ({or_clauses}) LIMIT ?"
    params: list[Any] = [workspace_id]
    params.extend([pattern] * len(cols))
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    hits: list[SearchHit] = []
    for row in rows:
        projection = project(kind, row)
        if projection is None:
            continue
        hits.append(SearchHit(kind=kind, projection=projection, score=0.5))
    return hits


def search_chunks_fts(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str,
    limit: int = 10,
) -> list[SearchHit]:
    """BM25 search across chunks_fts. Returns chunk projections."""
    sql = (
        "SELECT c.* FROM chunks_fts f "
        "JOIN chunks c ON c.id = f.chunk_id "
        "WHERE f.chunks_fts MATCH ? AND f.workspace_id = ? "
        "ORDER BY rank LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (query, workspace_id, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    hits: list[SearchHit] = []
    for row in rows:
        projection = project("chunk", row)
        if projection is None:
            continue
        hits.append(SearchHit(kind="chunk", projection=projection, score=0.8))
    return hits


def search(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str,
    kinds: list[str] | None = None,
    limit: int = 10,
    rerank: bool = False,
) -> list[SearchHit]:
    """Multi-kind search. Returns compact projections sorted by relevance.

    By default searches across all known kinds. Pass ``kinds=['decision',
    'behavior']`` to narrow scope. Per-kind limit divides ``limit`` so
    a wide search returns balanced results, not all one kind.

    ``rerank=True`` runs the optional cross-encoder reranker over the
    initial hit set. Requires the ``[rerank]`` extra; falls back to the
    BM25/LIKE order if the model is unavailable.
    """
    if not query.strip():
        return []
    selected = kinds or list(_KIND_TABLES.keys())
    per_kind = max(2, limit // max(1, len(selected)))
    hits: list[SearchHit] = []
    for kind in selected:
        if kind == "chunk":
            hits.extend(
                search_chunks_fts(conn, workspace_id=workspace_id, query=query, limit=per_kind)
            )
        else:
            hits.extend(
                search_kind(conn, workspace_id=workspace_id, kind=kind, query=query, limit=per_kind)
            )
    hits.sort(key=lambda h: h.score, reverse=True)
    if rerank and hits:
        from agent_memory_lite.retrieval.rerank import rerank_hits  # noqa: PLC0415

        return rerank_hits(query, hits, top_k=limit)
    return hits[:limit]


# ============================================================
# Counts (used by memory_brief composition)
# ============================================================


def count_kind(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    pinned_only: bool = False,
    status: str | None = None,
) -> int:
    """Cheap COUNT(*) for the brief composer + audit hooks."""
    if kind not in _KIND_TABLES:
        return 0
    table, _ = _KIND_TABLES[kind]
    where = ["workspace_id = ?"]
    params: list[Any] = [workspace_id]
    if pinned_only and kind in ("decision", "behavior"):
        where.append("pinned = 1")
    if status:
        where.append("status = ?")
        params.append(status)
    sql = f"SELECT COUNT(*) FROM {table} WHERE {' AND '.join(where)}"
    return int(conn.execute(sql, params).fetchone()[0])
