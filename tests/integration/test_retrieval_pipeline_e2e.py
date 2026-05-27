"""Integration: ingest -> memory_search exercises the compact retrieval path."""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.storage.reader import search

pytestmark = pytest.mark.integration


def _episode(text: str) -> EpisodeIn:
    return EpisodeIn(
        workspace_id="default",
        source_type=EpisodeSource.AGENT_ACTION,
        raw_text=text,
        trust_level=TrustLevel.AGENT_OBSERVED,
        importance=0.7,
    )


def test_compact_search_finds_recent_episode(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    result = ingest_episode(
        applied_conn,
        _episode("Wired RRF fusion across FTS + vector signals."),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    hits = search(
        applied_conn,
        workspace_id="default",
        query="RRF fusion vector signals",
        kinds=["chunk"],
        limit=8,
    )

    assert hits, "expected at least one retrieval hit"
    assert result.chunk.id in [hit.projection["id"] for hit in hits]


def test_pipeline_without_provider_falls_back_to_fts(
    applied_conn: sqlite3.Connection,
) -> None:
    ingest_episode(
        applied_conn,
        _episode("FTS-only retrieval still works without vector backend."),
    )
    hits = search(
        applied_conn,
        workspace_id="default",
        query="FTS retrieval",
        kinds=["chunk"],
        limit=8,
    )
    assert hits
    assert all(hit.kind == "chunk" for hit in hits)


def test_workspace_isolation_in_retrieval(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    result = ingest_episode(
        applied_conn,
        _episode("default workspace content"),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )
    other = EpisodeIn(
        workspace_id="other",
        source_type=EpisodeSource.AGENT_ACTION,
        raw_text="other workspace content",
        trust_level=TrustLevel.AGENT_OBSERVED,
        importance=0.5,
    )
    ingest_episode(
        applied_conn,
        other,
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    hits = search(
        applied_conn,
        workspace_id="default",
        query="content",
        kinds=["chunk"],
        limit=8,
    )
    assert result.chunk.id in [hit.projection["id"] for hit in hits]
    assert not any("other workspace" in str(hit.projection) for hit in hits)
