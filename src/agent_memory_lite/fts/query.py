"""BM25-ordered search across `chunks_fts`."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

# Characters with FTS5 special meaning (operators, quoting). We strip them and
# fall back to a quoted phrase or token list to avoid syntax errors from
# user-supplied queries.
_FTS_SPECIAL = re.compile(r'[\\"():*^+\-]')


@dataclass(frozen=True, slots=True)
class ChunkFtsHit:
    chunk_id: str
    workspace_id: str
    score: float  # bm25 -- lower is better; we negate when surfacing
    path: str
    text: str
    summary: str | None


def _sanitize(query: str) -> str:
    cleaned = _FTS_SPECIAL.sub(" ", query)
    tokens = [tok for tok in cleaned.split() if tok]
    if not tokens:
        return ""
    return " OR ".join(f'"{tok}"' for tok in tokens)


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
    rows = conn.execute(
        """
        SELECT
            chunks_fts.chunk_id   AS chunk_id,
            chunks_fts.workspace_id AS workspace_id,
            bm25(chunks_fts)      AS score,
            chunks_fts.path       AS path,
            chunks_fts.text       AS text,
            chunks_fts.summary    AS summary
        FROM chunks_fts
        WHERE chunks_fts MATCH ?
          AND workspace_id = ?
        ORDER BY score
        LIMIT ?
        """,
        (safe, workspace_id, limit),
    ).fetchall()
    return [
        ChunkFtsHit(
            chunk_id=str(row["chunk_id"]),
            workspace_id=str(row["workspace_id"]),
            score=float(row["score"]),
            path=str(row["path"] or ""),
            text=str(row["text"]),
            summary=row["summary"] if row["summary"] else None,
        )
        for row in rows
    ]
