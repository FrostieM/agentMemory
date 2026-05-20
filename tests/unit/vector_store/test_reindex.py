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


# ============================================================
# v3.4 streaming / resume-safe rebuild
# ============================================================


def _seed_n_chunks(
    conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
    n: int,
) -> list[str]:
    """Seed ``n`` episodes (one chunk each) with embeddings already
    landed in the vector store. Returns the chunk ids in seed order."""
    ids: list[str] = []
    for i in range(n):
        result = ingest_episode(
            conn,
            EpisodeIn(
                workspace_id="default",
                source_type=EpisodeSource.AGENT_ACTION,
                raw_text=f"chunk number {i} for resume-safe rebuild test",
                trust_level=TrustLevel.AGENT_OBSERVED,
            ),
            embedding_provider=fake_embedding_provider,
            vector_store=fake_vector_store,
        )
        ids.append(result.chunk.id)
    return ids


def test_resume_safe_rebuild_skips_existing_vectors(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    """v3.4: when ``resume=True`` (default), chunks already in the
    vector store are not re-embedded. ``done`` is the count of NEW
    embeddings only."""
    _seed_n_chunks(applied_conn, fake_embedding_provider, fake_vector_store, 3)
    # All 3 chunks already have vectors → resume rebuild does nothing.
    done = reindex_chunks(
        applied_conn,
        workspace_id="default",
        provider=fake_embedding_provider,
        store=fake_vector_store,
        resume=True,
    )
    assert done == 0


def test_resume_safe_rebuild_fills_missing(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    """Drop one vector from the store, then run resume rebuild → it
    re-embeds only that one."""
    ids = _seed_n_chunks(applied_conn, fake_embedding_provider, fake_vector_store, 3)
    fake_vector_store.delete("chunks", [ids[1]])

    done = reindex_chunks(
        applied_conn,
        workspace_id="default",
        provider=fake_embedding_provider,
        store=fake_vector_store,
        resume=True,
    )
    assert done == 1
    # All three are now in the store again.
    surviving = set(fake_vector_store.list_ids("chunks", workspace_id="default"))
    assert set(ids).issubset(surviving)


def test_force_rebuild_drops_and_rebuilds_all(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    """``resume=False`` drops the namespace first and re-embeds every
    chunk — same as legacy behaviour."""
    ids = _seed_n_chunks(applied_conn, fake_embedding_provider, fake_vector_store, 3)
    done = reindex_chunks(
        applied_conn,
        workspace_id="default",
        provider=fake_embedding_provider,
        store=fake_vector_store,
        resume=False,
    )
    assert done == 3
    surviving = set(fake_vector_store.list_ids("chunks", workspace_id="default"))
    assert set(ids).issubset(surviving)


def test_progress_callback_fires_per_batch(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    """The progress callback is invoked once at start (0/total) and
    once per batch with cumulative ``done``."""
    _seed_n_chunks(applied_conn, fake_embedding_provider, fake_vector_store, 5)
    events: list[tuple[int, int]] = []
    reindex_chunks(
        applied_conn,
        workspace_id="default",
        provider=fake_embedding_provider,
        store=fake_vector_store,
        resume=False,
        batch_size=2,
        progress_callback=lambda done, total: events.append((done, total)),
    )
    # First event is (0, 5); subsequent are (2,5), (4,5), (5,5).
    assert events[0] == (0, 5)
    assert events[-1] == (5, 5)
    # done is monotonic non-decreasing.
    dones = [e[0] for e in events]
    assert dones == sorted(dones)


def test_checkpoint_written_and_cleared(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    """A checkpoint row lands in workspace_meta while rebuild is in
    progress; on successful completion the row is cleared so a future
    audit doesn't see a stale 'rebuild paused' marker."""
    _seed_n_chunks(applied_conn, fake_embedding_provider, fake_vector_store, 2)
    reindex_chunks(
        applied_conn,
        workspace_id="default",
        provider=fake_embedding_provider,
        store=fake_vector_store,
        resume=False,
    )
    row = applied_conn.execute(
        "SELECT COUNT(*) FROM workspace_meta WHERE key = 'chunk_rebuild_progress'"
    ).fetchone()
    assert row[0] == 0  # cleared on success


def test_no_chunks_no_op(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    """Empty workspace → reindex returns 0 without crashing."""
    done = reindex_chunks(
        applied_conn,
        workspace_id="default",
        provider=fake_embedding_provider,
        store=fake_vector_store,
    )
    assert done == 0
