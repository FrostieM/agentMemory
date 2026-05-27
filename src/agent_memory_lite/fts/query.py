"""BM25-ordered search across `chunks_fts`."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

# Characters with FTS5 special meaning (operators, quoting). We strip them and
# fall back to a quoted phrase or token list to avoid syntax errors from
# user-supplied queries.
_FTS_SPECIAL = re.compile(r'[\\"():*^+\-]')
_ASCII_ANCHOR = re.compile(r"[A-Za-z0-9_.-]+")
# v3.5 sector-2 audit-followup: cap token count + total length to prevent
# query DoS (a 10KB query expanded to thousands of OR-terms ran a
# per-token postings scan on the entire FTS5 index).
_MAX_FTS_TOKENS = 32
_MAX_FTS_TOTAL_CHARS = 256


@dataclass(frozen=True, slots=True)
class ChunkFtsHit:
    chunk_id: str
    workspace_id: str
    score: float  # bm25 -- lower is better; we negate when surfacing
    path: str
    text: str
    summary: str | None
    created_at: str
    kind: str
    episode_id: str | None
    is_archived: bool = False


def _sanitize(query: str) -> str:
    """Build an FTS5 MATCH expression that AND-joins tokens.

    v3.5 sector-2 audit-followup: the previous implementation OR-joined
    every token (`'"foo" OR "bar" OR "baz"'`). On BM25 retrieval that
    devolves to "match ANY token" and crashes precision — directly
    responsible for the NDCG@10=0.6694 plateau on BEIR SciFact. The
    new behaviour matches what users expect from a search box:
    multi-token queries narrow the result set, not widen it. AND is
    expressed as space-separated quoted phrases (FTS5 default).
    """
    cleaned = _FTS_SPECIAL.sub(" ", query)
    if len(cleaned) > _MAX_FTS_TOTAL_CHARS:
        cleaned = cleaned[:_MAX_FTS_TOTAL_CHARS]
    tokens = [tok for tok in cleaned.split() if tok][:_MAX_FTS_TOKENS]
    if not tokens:
        return ""
    return " ".join(f'"{tok}"' for tok in tokens)


def _mixed_language_anchor_query(query: str) -> str:
    if query.isascii():
        return ""
    return " ".join(_ASCII_ANCHOR.findall(query))


def _search_safe(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    safe: str,
    limit: int,
) -> list[sqlite3.Row]:
    if not safe:
        return []
    try:
        return conn.execute(
            """
            SELECT
                chunks_fts.chunk_id   AS chunk_id,
                chunks_fts.workspace_id AS workspace_id,
                bm25(chunks_fts)      AS score,
                chunks_fts.path       AS path,
                chunks_fts.text       AS text,
                chunks_fts.summary    AS summary,
                chunks.created_at     AS created_at,
                chunks.kind           AS kind,
                chunks.episode_id     AS episode_id,
                chunks.is_archived    AS is_archived
            FROM chunks_fts
            JOIN chunks ON chunks.id = chunks_fts.chunk_id
            WHERE chunks_fts MATCH ?
              AND chunks_fts.workspace_id = ?
            ORDER BY score
            LIMIT ?
            """,
            (safe, workspace_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def search_chunks_fts(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str,
    limit: int = 30,
) -> list[ChunkFtsHit]:
    safe = _sanitize(query)
    if not safe:
        return []
    # v3.5 sector-2 audit-followup: tolerate FTS5 syntax errors. The
    # sanitizer strips known operators but the BM25 grammar evolves;
    # an uncaught ``sqlite3.OperationalError`` previously escaped to
    # the FastAPI layer as 500 (same blast radius as the v3.4 enum
    # drift). On unparseable input fall back to empty result + retry
    # with the LIKE-style trigram path inside the caller (if any).
    rows = _search_safe(conn, workspace_id=workspace_id, safe=safe, limit=limit)
    if not rows:
        anchor_safe = _sanitize(_mixed_language_anchor_query(query))
        if anchor_safe and anchor_safe != safe:
            rows = _search_safe(conn, workspace_id=workspace_id, safe=anchor_safe, limit=limit)
    return [
        ChunkFtsHit(
            chunk_id=str(row["chunk_id"]),
            workspace_id=str(row["workspace_id"]),
            score=float(row["score"]),
            path=str(row["path"] or ""),
            text=str(row["text"]),
            summary=row["summary"] if row["summary"] else None,
            created_at=str(row["created_at"] or ""),
            kind=str(row["kind"] or ""),
            episode_id=str(row["episode_id"]) if row["episode_id"] else None,
            is_archived=bool(row["is_archived"]) if row["is_archived"] is not None else False,
        )
        for row in rows
    ]
