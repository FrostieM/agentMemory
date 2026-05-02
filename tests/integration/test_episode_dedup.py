"""Integration: episode_dedup short-circuits near-duplicate ingest."""

from __future__ import annotations

import sqlite3

import pytest
from tests.conftest import FakeEmbeddingProvider, FakeVectorStore

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn

pytestmark = pytest.mark.integration


def _episode(text: str) -> EpisodeIn:
    return EpisodeIn(
        workspace_id="qa",
        source_type=EpisodeSource.AGENT_ACTION,
        raw_text=text,
        trust_level=TrustLevel.AGENT_OBSERVED,
        importance=0.5,
    )


def _settings(enabled: bool = True, threshold: float = 0.95) -> Settings:
    return Settings(
        MEMORY_EPISODE_DEDUP_ENABLED="1" if enabled else "0",
        MEMORY_EPISODE_DEDUP_THRESHOLD=str(threshold),
        MEMORY_EPISODE_DEDUP_WINDOW="20",
    )


def test_dedup_returns_existing_episode_for_identical_text(
    applied_conn: sqlite3.Connection,
) -> None:
    provider = FakeEmbeddingProvider()
    store = FakeVectorStore()
    settings = _settings()

    first = ingest_episode(
        applied_conn,
        _episode("Heap watchdog timer disabled in production logging path"),
        embedding_provider=provider,
        vector_store=store,
        auto_promote_settings=settings,
    )
    assert first.was_duplicate is False
    assert first.embedded is True

    second = ingest_episode(
        applied_conn,
        _episode("Heap watchdog timer disabled in production logging path"),
        embedding_provider=provider,
        vector_store=store,
        auto_promote_settings=settings,
    )
    assert second.was_duplicate is True
    assert second.episode.id == first.episode.id
    assert second.chunk.id == first.chunk.id
    assert second.duplicate_similarity >= 0.95


def test_dedup_disabled_writes_two_rows(applied_conn: sqlite3.Connection) -> None:
    provider = FakeEmbeddingProvider()
    store = FakeVectorStore()
    settings = _settings(enabled=False)

    first = ingest_episode(
        applied_conn,
        _episode("Same exact text"),
        embedding_provider=provider,
        vector_store=store,
        auto_promote_settings=settings,
    )
    second = ingest_episode(
        applied_conn,
        _episode("Same exact text"),
        embedding_provider=provider,
        vector_store=store,
        auto_promote_settings=settings,
    )
    assert first.episode.id != second.episode.id
    assert second.was_duplicate is False
