from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.maintenance.retrieval_quality import (
    RetrievalQualityCase,
    run_retrieval_quality_evals,
)
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn


def test_retrieval_quality_detects_expected_chunk_and_sources(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    result = ingest_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="project-a",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="heap_watchdog v2 fixed state refresh memory pressure",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    report = run_retrieval_quality_evals(
        applied_conn,
        workspace_id="project-a",
        cases=[
            RetrievalQualityCase(
                name="heap_watchdog",
                query="heap_watchdog memory pressure",
                expected_ids=[result.chunk.id],
                expected_sources=["fts", "vector"],
                top_k=3,
            )
        ],
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    assert report.status == "ok"
    assert report.cases_passed == 1


def test_retrieval_quality_degrades_on_missing_expected_id(
    applied_conn: sqlite3.Connection,
) -> None:
    report = run_retrieval_quality_evals(
        applied_conn,
        workspace_id="project-a",
        cases=[
            RetrievalQualityCase(
                name="missing",
                query="no such query",
                expected_ids=["chk_missing"],
                top_k=3,
            )
        ],
    )

    assert report.status == "degraded"
    assert report.failures
