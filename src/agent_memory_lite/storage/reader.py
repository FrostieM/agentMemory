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
from agent_memory_lite.utils.text_encoding import repair_common_mojibake

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
    "issue": ("issues", "id"),
    "snapshot": ("snapshots", "id"),
    "code_digest": ("code_digests", "id"),
    "chunk": ("chunks", "id"),
    "plan_step": ("plan_steps", "id"),
}
VALID_KINDS = frozenset(_KIND_TABLES)
_STATUS_ARCHIVE_KINDS = {"decision", "theory", "insight"}


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
    "issue": ["title", "gist", "description"],
    "snapshot": ["snapshot_key", "title", "source_label", "db_path", "duckdb_path"],
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


def validate_kinds(kinds: list[str]) -> list[str]:
    unknown = sorted(set(kinds) - VALID_KINDS)
    if unknown:
        raise ValueError(f"unknown kinds: {unknown}; valid kinds: {sorted(VALID_KINDS)}")
    return kinds


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(str(row[1]) == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _append_archive_filter(
    conn: sqlite3.Connection,
    *,
    table: str,
    kind: str,
    where: list[str],
    requested_status: str | None = None,
) -> None:
    """Hide archived rows from discovery while keeping exact id fetch intact."""
    if _table_has_column(conn, table, "is_archived"):
        where.append("COALESCE(is_archived, 0) = 0")
    if (
        requested_status is None
        and kind in _STATUS_ARCHIVE_KINDS
        and _table_has_column(conn, table, "status")
    ):
        where.append("COALESCE(status, '') != 'archived'")


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
    _append_archive_filter(conn, table=table, kind=kind, where=where, requested_status=status)
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


def plan_for_task(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    task_id: str,
) -> list[dict[str, Any]]:
    """List one plan's live steps as compact projections, rank-ordered.

    The read companion to the plan_step write surface: a single call
    returns every live (``valid_to IS NULL``) step of ``task_id`` -- id,
    title, status, rank -- so the agent re-finds its plan instead of
    tracking step ids by hand. Re-planned-out steps are excluded.

    Always now-only -- there is no ``as_of`` knob; ``valid_to IS NULL``
    is a deliberate "current plan" filter. ``rank`` is not unique, so
    ties break deterministically on ``created_at`` then ``id``; the
    result is capped at 500 steps (far above any real plan) to bound
    the envelope.
    """
    rows = conn.execute(
        "SELECT * FROM plan_steps "
        "WHERE workspace_id = ? AND task_id = ? AND valid_to IS NULL "
        "ORDER BY rank, created_at, id "
        "LIMIT 500",
        (workspace_id, task_id),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        projection = project("plan_step", row)
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
            value = row[field]
        except (KeyError, IndexError):
            continue
        if (
            kind == "decision"
            and field in {"title", "decision_text", "rationale"}
            and isinstance(value, str)
        ):
            value = repair_common_mojibake(value)
        extras[field] = value
    return {**base, **extras}


# ============================================================
# memory_search — hybrid BM25 over FTS5 + per-kind fallback
# ============================================================


# A query token shorter than this is too noisy for a LIKE scan (every
# row contains "a"); 2 keeps meaningful short tokens like "ui" / "db".
_MIN_SEARCH_TOKEN_LEN = 2
# Cap on query tokens — bounds the generated SQL (one CASE arm per
# token), mirroring the chunk path's _sanitize length cap.
_MAX_SEARCH_TOKENS = 32
# Punctuation trimmed from each token so "plan_steps." matches the bare
# word in stored text.
_TOKEN_TRIM = ".,;:!?()[]{}\"'`"


def _search_tokens(query: str) -> list[str]:
    """Whitespace-split a query into distinct lower-cased LIKE tokens.

    Order-preserving dedup; punctuation trimmed from each token; tokens
    shorter than ``_MIN_SEARCH_TOKEN_LEN`` dropped as too noisy; the
    list capped at ``_MAX_SEARCH_TOKENS`` so a pathological query cannot
    explode the generated SQL.
    """
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in query.lower().split():
        tok = raw.strip(_TOKEN_TRIM)
        if len(tok) >= _MIN_SEARCH_TOKEN_LEN and tok not in seen:
            seen.add(tok)
            tokens.append(tok)
            if len(tokens) >= _MAX_SEARCH_TOKENS:
                break
    return tokens


def _like_pattern(token: str) -> str:
    """A LIKE pattern matching ``token`` as a literal substring.

    ``\\`` / ``%`` / ``_`` inside the token are escaped so a token like
    ``plan_steps`` matches a literal underscore, not LIKE's any-char
    wildcard. Pair with ``LIKE ? ESCAPE '\\'``.
    """
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_kind(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    query: str,
    limit: int = 10,
) -> list[SearchHit]:
    """Token-overlap search across the kind's free-text columns.

    Each query token is matched independently with an escaped LIKE
    substring scan over ``_KIND_FTS_COLUMNS[kind]``. The distinct-token
    match count is computed IN SQL and used for ``ORDER BY``, so the
    ``LIMIT`` keeps the genuine top matches rather than an arbitrary
    candidate window. The returned score is ``0.5 * matched / tokens``
    — within (0, 0.5], deliberately below the chunk-FTS score band so
    this interim path never outranks a real BM25 chunk hit.

    The prior implementation LIKE-matched the *whole query* as one
    substring, so any multi-word query missed every row lacking that
    verbatim phrase — which silently defeated ``memory_search`` for
    decisions / theories / behaviors (chunks have their own FTS5 path).
    A future plan adds FTS5 + rerank to these kinds.
    """
    if kind not in _KIND_TABLES or kind not in _KIND_FTS_COLUMNS:
        return []
    tokens = _search_tokens(query)
    if not tokens:
        return []
    table, _ = _KIND_TABLES[kind]
    cols = _KIND_FTS_COLUMNS[kind]
    where = ["workspace_id = ?"]
    where_params: list[Any] = [workspace_id]
    _append_archive_filter(conn, table=table, kind=kind, where=where)
    # Per token: 1 when it appears in ANY searchable column. The sum is
    # the row's distinct-token match count; ORDER BY it so LIMIT keeps
    # the true top matches, not an arbitrary candidate window.
    col_likes = " OR ".join(f"LOWER(IFNULL({c}, '')) LIKE ? ESCAPE '\\'" for c in cols)
    case_arms: list[str] = []
    params: list[Any] = []
    for tok in tokens:
        case_arms.append(f"(CASE WHEN ({col_likes}) THEN 1 ELSE 0 END)")
        params.extend([_like_pattern(tok)] * len(cols))
    score_expr = " + ".join(case_arms)
    params.extend(where_params)
    params.append(limit)
    sql = (
        f"SELECT *, ({score_expr}) AS _match_score FROM {table} "
        f"WHERE {' AND '.join(where)} ORDER BY _match_score DESC LIMIT ?"
    )
    rows = conn.execute(sql, params).fetchall()
    hits: list[SearchHit] = []
    for row in rows:
        matched = int(row["_match_score"])
        if matched == 0:
            continue
        projection = project(kind, row)
        if projection is None:
            continue
        score = 0.5 * matched / len(tokens)
        hits.append(SearchHit(kind=kind, projection=projection, score=score))
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
        "AND COALESCE(c.is_archived, 0) = 0 "
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
    'behavior']`` to narrow scope. A default wide search divides ``limit``
    across all kinds so results stay balanced (not all one kind). An EXPLICIT
    ``kinds`` list is a focused query, so each requested kind gets the full
    ``limit`` budget (#120) -- a search narrowed to one/few kinds can then
    return up to ``limit`` of them instead of being capped at ``limit // N``.

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
    selected = list(_KIND_TABLES.keys()) if kinds is None else validate_kinds(kinds)
    if not selected:
        return []
    # #120: an explicit kinds=[...] is a focused query -> full budget per
    # requested kind, so the global top-`limit` is not distorted by a per-kind
    # cap. Default wide search keeps the divided budget (balance + bounded scan).
    per_kind = limit if kinds is not None else max(2, limit // max(1, len(selected)))
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
    _append_archive_filter(conn, table=table, kind=kind, where=where, requested_status=status)
    if pinned_only and kind in ("decision", "behavior"):
        where.append("pinned = 1")
    if status:
        where.append("status = ?")
        params.append(status)
    sql = f"SELECT COUNT(*) FROM {table} WHERE {' AND '.join(where)}"
    return int(conn.execute(sql, params).fetchone()[0])
