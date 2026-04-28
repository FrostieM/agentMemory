from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.vector_store.reindex import reindex_chunks, repair_chunk_embedding_refs


def test_reindex_sets_chunk_embedding_references(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    result = ingest_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="default",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="reindex should backfill embedding references",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
    )
    assert result.chunk.embedding_id is None

    total = reindex_chunks(
        applied_conn,
        workspace_id="default",
        provider=fake_embedding_provider,
        store=fake_vector_store,
    )

    row = applied_conn.execute(
        "SELECT embedding_id FROM chunks WHERE id = ?",
        (result.chunk.id,),
    ).fetchone()
    assert total == 1
    assert row["embedding_id"] == result.chunk.id


def test_repair_chunk_embedding_refs_uses_existing_vectors(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    result = ingest_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="default",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="embedding refs can be repaired without reindexing",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )
    assert result.chunk.embedding_id == result.chunk.id
    applied_conn.execute("UPDATE chunks SET embedding_id = NULL WHERE id = ?", (result.chunk.id,))

    repaired = repair_chunk_embedding_refs(
        applied_conn,
        workspace_id="default",
        store=fake_vector_store,
    )

    row = applied_conn.execute(
        "SELECT embedding_id FROM chunks WHERE id = ?",
        (result.chunk.id,),
    ).fetchone()
    assert repaired == 1
    assert row["embedding_id"] == result.chunk.id
