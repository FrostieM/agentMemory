from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.maintenance.feedback_report import run_usage_feedback_report
from agent_memory_lite.maintenance.usage_feedback import (
    chunk_feedback_boosts,
    record_usage_feedback,
)
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.context_builder import build_context


def test_usage_feedback_is_recorded_and_boosted(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    result = ingest_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="project-a",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="heap_watchdog exact sentinel episode",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    feedback = record_usage_feedback(
        applied_conn,
        workspace_id="project-a",
        source_type="chunk",
        source_id=result.chunk.id,
        query="heap watchdog",
        usefulness=1.0,
        task_id="task-1",
        notes="useful",
    )

    assert feedback.id.startswith("uf_")
    assert (
        chunk_feedback_boosts(
            applied_conn,
            workspace_id="project-a",
            chunk_ids=[result.chunk.id],
        )[result.chunk.id]
        > 0
    )

    context = build_context(
        applied_conn,
        RetrievalQuery(workspace_id="project-a", query="heap_watchdog", max_tokens=1200),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    assert context.hits[0].metadata["usage_feedback_boost"] > 0


def test_usage_feedback_report_summarizes_noisy_and_helpful_sources(
    applied_conn: sqlite3.Connection,
) -> None:
    record_usage_feedback(
        applied_conn,
        workspace_id="project-a",
        source_type="chunk",
        source_id="chk_noise",
        query="bad context",
        usefulness=-1.0,
        notes="stale noise",
    )
    record_usage_feedback(
        applied_conn,
        workspace_id="project-a",
        source_type="chunk",
        source_id="chk_noise",
        query="bad context",
        usefulness=-0.8,
        notes="still noisy",
    )
    record_usage_feedback(
        applied_conn,
        workspace_id="project-a",
        source_type="decision",
        source_id="dec_useful",
        query="architecture",
        usefulness=0.9,
        notes="useful",
    )
    record_usage_feedback(
        applied_conn,
        workspace_id="project-a",
        source_type="decision",
        source_id="dec_useful",
        query="architecture",
        usefulness=0.7,
        notes="useful again",
    )

    report = run_usage_feedback_report(applied_conn, workspace_id="project-a")

    assert report.status == "warning"
    assert report.counts["total"] == 4
    assert report.counts["noisy_sources"] == 1
    assert report.counts["helpful_sources"] == 1
    assert report.noisy_sources[0].source_id == "chk_noise"
    assert report.helpful_sources[0].source_id == "dec_useful"


def test_usage_feedback_report_is_ok_when_empty(applied_conn: sqlite3.Connection) -> None:
    report = run_usage_feedback_report(applied_conn, workspace_id="project-a")

    assert report.status == "warning"
    assert report.counts["total"] == 0
    assert report.warnings == ["no usage feedback recorded yet"]
