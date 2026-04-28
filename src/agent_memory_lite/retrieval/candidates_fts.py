"""FTS candidate fetcher.

Wraps `fts.search_chunks_fts` and converts the dialect-specific hit type into
the uniform `RetrievalCandidate` used by the rest of the pipeline.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.models.retrieval import RetrievalCandidate

DEFAULT_LIMIT = 30


def collect_fts(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str,
    limit: int = DEFAULT_LIMIT,
) -> list[RetrievalCandidate]:
    hits = search_chunks_fts(
        conn,
        workspace_id=workspace_id,
        query=query,
        limit=limit,
    )
    return [
        RetrievalCandidate(
            id=hit.chunk_id,
            workspace_id=hit.workspace_id,
            source="fts",
            text=hit.text,
            path=hit.path,
            summary=hit.summary,
            raw_score=-hit.score,  # bm25 lower-is-better; flip sign for "higher = better"
            metadata={"path": hit.path, "fts_rank": rank, "fts_bm25": hit.score},
        )
        for rank, hit in enumerate(hits)
    ]
