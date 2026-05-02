"""Integration: ingest → build_context exercises the full hybrid pipeline."""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.context_builder import build_context

pytestmark = pytest.mark.integration


def _episode(text: str) -> EpisodeIn:
    return EpisodeIn(
        workspace_id="default",
        source_type=EpisodeSource.AGENT_ACTION,
        raw_text=text,
        trust_level=TrustLevel.AGENT_OBSERVED,
        importance=0.7,
    )


def test_hybrid_pipeline_finds_recent_episode(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    ingest_episode(
        applied_conn,
        _episode("Wired RRF fusion across FTS + vector signals."),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    built = build_context(
        applied_conn,
        RetrievalQuery(workspace_id="default", query="RRF fusion vector signals"),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    assert built.hits, "expected at least one retrieval hit"
    assert any("RRF" in hit.text or "fusion" in hit.text for hit in built.hits)
    assert "<memory_context>" in built.text


def test_pipeline_without_provider_falls_back_to_fts(
    applied_conn: sqlite3.Connection,
) -> None:
    ingest_episode(
        applied_conn,
        _episode("FTS-only retrieval still works without vector backend."),
    )
    built = build_context(
        applied_conn,
        RetrievalQuery(workspace_id="default", query="FTS retrieval"),
    )
    assert built.hits
    assert all("fts" in hit.sources for hit in built.hits)


def test_workspace_isolation_in_retrieval(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    ingest_episode(
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

    built = build_context(
        applied_conn,
        RetrievalQuery(workspace_id="default", query="content"),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )
    assert all("default" in hit.text or "default" in hit.workspace_id for hit in built.hits)
    assert not any("other workspace" in hit.text for hit in built.hits)
