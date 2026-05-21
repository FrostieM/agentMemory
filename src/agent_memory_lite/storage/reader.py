"""Reader API — memory_view / memory_get / memory_search.

These are the read-side primitives all agent surfaces (MCP, HTTP, CLI)
delegate to. They share three invariants:

1. **Compact projections by default.** Every result row goes through
   ``projections.project()`` before return. ~20-40 tokens per item.
2. **Selective field fetch.** ``memory_get(id, fields=[...])`` returns
   exactly the requested columns (plus id/kind). Default = projection.
3. **Read-only.** No mutations. Writer module owns those.

This module knows the schema but does NOT depend on FastAPI, MCP,
or any agent runtime. It's pure SQL + dict transforms.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from agent_memory_lite.storage.projections import project

_LOG = logging.getLogger(__name__)

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
    "plan_step": ("plan_steps", "id"),
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
    "plan_step": ["title", "body"],
}


# ============================================================
# memory_view — directory listing / single-row fetch
# ============================================================


_OUTCOME_KINDS = {"decision", "theory", "behavior", "skill", "insight", "chunk"}
# Phase 6: kinds that carry bi-temporal validity columns.
_BI_TEMPORAL_KINDS = {"decision", "theory", "behavior", "concept", "insight", "plan_step"}


def _require_known_kind(kind: str) -> None:
    """Round-2 audit: an unknown ``kind`` used to make ``list_kind`` /
    ``get_object`` return ``[]`` / ``None`` — indistinguishable from a
    genuine empty result. That is the v3.4 enum-drift class of bug: a
    plural typo (``memory_get(kind="decisions")``) or a renamed kind
    silently "finds nothing" and the agent concludes memory is empty.
    Raise instead so the typo / drift is loud at the call site.

    Scoped to the direct-call primitives only — internal fan-out paths
    (``search_kind``, ``get_objects_batch``) stay lenient because they
    iterate kinds and a per-kind skip is the correct degrade there."""
    if kind not in _KIND_TABLES:
        raise ValueError(f"unknown kind {kind!r}; valid kinds: {sorted(_KIND_TABLES)}")


def list_kind(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    limit: int = 20,
    pinned_only: bool = False,
    status: str | None = None,
    min_outcome: float = -1.0,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    """List rows of ``kind`` as compact projections.

    Equivalent to the v2 ``_index.md`` per-kind listing concept, but
    cheap and indexed.

    ``min_outcome`` (Phase 1) filters rows by the denormalized
    ``outcome_score`` column. Default ``-1.0`` means "show everything"
    so existing callers stay byte-equivalent. Pass ``0.0`` to drop
    failed approaches from the result set. Silently no-op when the
    column does not exist (pre-migration DB).

    ``as_of`` (Phase 6) filters rows by bi-temporal validity:
    ``valid_from <= as_of AND (valid_to IS NULL OR valid_to > as_of)``.
    Default ``None`` means "now" -- pass an ISO 8601 string to ask
    "what did the workspace believe at this point in time". Silently
    no-op on tables that lack the validity columns (pre-migration DB
    or kinds outside ``_BI_TEMPORAL_KINDS``).
    """
    _require_known_kind(kind)
    table, _ = _KIND_TABLES[kind]
    where = ["workspace_id = ?"]
    params: list[Any] = [workspace_id]
    if pinned_only and kind in ("decision", "behavior"):
        where.append("pinned = 1")
    if status:
        where.append("status = ?")
        params.append(status)
    if min_outcome > -1.0 and kind in _OUTCOME_KINDS:
        where.append("COALESCE(outcome_score, 0.0) >= ?")
        params.append(min_outcome)
    # Phase 6: bi-temporal filter when the table has the columns AND
    # the master flag is on. Off-path = byte-equivalent to v3.0.0-base
    # (no validity bracket applied even on a migrated DB).
    if kind in _BI_TEMPORAL_KINDS:
        try:
            from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415

            bi_temporal_on = get_settings().bi_temporal_enabled
        except Exception:  # pragma: no cover - defensive
            bi_temporal_on = True
        if bi_temporal_on:
            from agent_memory_lite.storage.bi_temporal import (  # noqa: PLC0415
                has_validity_columns,
                where_valid,
            )

            if has_validity_columns(conn, table):
                clause, vparams = where_valid(as_of=as_of)
                where.append(clause)
                params.extend(vparams)
    sql = f"SELECT * FROM {table} WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # Pre-migration DB without outcome_score; retry without the filter.
        if min_outcome > -1.0:
            return list_kind(
                conn,
                workspace_id=workspace_id,
                kind=kind,
                limit=limit,
                pinned_only=pinned_only,
                status=status,
                min_outcome=-1.0,
                as_of=as_of,
            )
        raise
    out: list[dict[str, Any]] = []
    for row in rows:
        projection = project(kind, row)
        if projection is not None:
            out.append(projection)
    return out


# ============================================================
# memory_get — fetch by id with selective fields
# ============================================================


def get_objects_batch(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    object_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Round-2 audit: batched ``get_object`` for the recall hot path.

    Replaces N single-row SELECTs with one IN-list query when callers
    already have a list of ids of the SAME kind. Returns a dict keyed
    by ``id_col`` value so callers can do O(1) lookup as they iterate
    their original ordering. Empty input -> empty dict. Unknown kind
    -> empty dict (mirrors ``get_object`` returning None per id).

    Compact projection only — selective ``fields=`` is intentionally
    NOT supported; callers that need full bodies have a single id and
    should use ``get_object`` directly. Batching is for the discovery
    path where projection is enough.
    """
    if not object_ids or kind not in _KIND_TABLES:
        return {}
    table, id_col = _KIND_TABLES[kind]
    # De-dup + cap to a safe IN-list size. SQLite's default
    # SQLITE_MAX_VARIABLE_NUMBER is 999 in older builds and 32766 in
    # 3.32+; ~500 is a comfortable ceiling that matches the recall
    # surface (max_nodes is limit*4, typically <50).
    unique = list(dict.fromkeys(object_ids))[:500]
    placeholders = ",".join("?" * len(unique))
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE workspace_id = ? AND {id_col} IN ({placeholders})",
        (workspace_id, *unique),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        projection = project(kind, row)
        if projection is None:
            continue
        # Prefer the id from the projection so we key on whatever
        # `project()` chose to surface (usually equal to id_col, but
        # stay defensive in case the projector renames).
        row_id = str(projection.get("id") or row[id_col])
        out[row_id] = projection
    return out


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
    _require_known_kind(kind)
    table, id_col = _KIND_TABLES[kind]
    row = conn.execute(
        f"SELECT * FROM {table} WHERE workspace_id = ? AND {id_col} = ?",
        (workspace_id, object_id),
    ).fetchone()
    if row is None:
        return None
    # Round-2 audit: a None from project() means a real projector bug
    # (the row EXISTS in SQL). The old ``or {...}`` silently swapped in
    # a thin stub so the bug looked like a valid thin result. Keep the
    # stub so the caller still gets the row, but log loudly so the
    # projector defect is visible instead of debugging-hostile.
    projected = project(kind, row)
    if projected is None:
        _LOG.warning(
            "project() returned None for kind=%r id=%r (row exists) — "
            "projector defect; returning id/kind stub",
            kind,
            object_id,
        )
        base: dict[str, Any] = {"id": object_id, "kind": kind}
    else:
        base = projected
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
    LIKE patterns. A future plan adds FTS5 + LanceDB + jina rerank to
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
    """BM25 search across chunks_fts. Returns chunk projections.

    Round-2 audit fix: the raw ``query`` used to reach FTS5 ``MATCH``
    directly. A query containing a bare FTS operator (``AND``/``OR``/
    ``NOT``/``*``/``-``/``(``) either silently returned zero chunk hits
    (the agent then concludes "no memory") or — for a query that DID
    parse — ran an attacker-shaped match expression. Now routed through
    the same ``fts.query._sanitize`` the canonical FTS path uses:
    operators stripped, token + length capped, AND-joined.
    """
    from agent_memory_lite.fts.query import _sanitize  # noqa: PLC0415

    safe = _sanitize(query)
    if not safe:
        return []
    sql = (
        "SELECT c.* FROM chunks_fts f "
        "JOIN chunks c ON c.id = f.chunk_id "
        "WHERE f.chunks_fts MATCH ? AND f.workspace_id = ? "
        "ORDER BY rank LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (safe, workspace_id, limit)).fetchall()
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
    log_coactivations: bool = True,
) -> list[SearchHit]:
    """Multi-kind search. Returns compact projections sorted by relevance.

    By default searches across all known kinds. Pass ``kinds=['decision',
    'behavior']`` to narrow scope. Per-kind limit divides ``limit`` so
    a wide search returns balanced results, not all one kind.

    ``rerank=True`` runs the optional cross-encoder reranker over the
    initial hit set. Requires the ``[rerank]`` extra; falls back to the
    BM25/LIKE order if the model is unavailable.

    ``log_coactivations`` (Phase 2 default ON) records the returned hit
    set into the ``retrieval_coactivation`` staging table; a sentinel
    sweep later distills co-occurrence into ``soft_edges``. Set False
    for internal callers that don't want their reads to feed the
    Hebbian loop (e.g. ``brief.compose_brief`` resolving associates).
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
    # v3.1: env-gated auto-rerank lets the operator turn on the cross-
    # encoder for every search without changing call sites. Explicit
    # ``rerank=True`` still wins; ``False`` + auto-flag-on → True.
    from agent_memory_lite.retrieval.rerank import (  # noqa: PLC0415
        auto_rerank_enabled,
        rerank_hits,
    )

    effective_rerank = rerank or auto_rerank_enabled()
    ranked = rerank_hits(query, hits, top_k=limit) if effective_rerank and hits else hits[:limit]
    if log_coactivations and ranked:
        _maybe_log_coactivation(conn, workspace_id, query, ranked)
    return ranked


def _maybe_log_coactivation(
    conn: sqlite3.Connection, workspace_id: str, query: str, hits: list[SearchHit]
) -> None:
    """Failure-soft coactivation side-effect. Gated on settings flag."""
    try:
        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415
        from agent_memory_lite.retrieval.coactivation_log import (  # noqa: PLC0415
            log_coactivation,
        )

        if not get_settings().hebbian_enabled:
            return
        log_coactivation(conn, workspace_id=workspace_id, query=query, hits=hits)
    except Exception:
        # Telemetry must never break the search hot path.
        return


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
