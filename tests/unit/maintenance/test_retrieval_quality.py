from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.canonical_writer import write_canonical
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
                expected_context_ids=[result.chunk.id],
                expected_sources=["fts"],
                top_k=3,
            )
        ],
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    assert report.status == "ok"
    assert report.cases_passed == 1
    assert report.recall_at_k == 1.0
    assert report.mrr == 1.0
    assert report.ndcg_at_k == 1.0
    assert report.context_hit_rate == 1.0
    assert report.results[0].matched_context_ids == [result.chunk.id]


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


def test_brief_budget_surfaces_relevant_decision_under_compact_surface(
    applied_conn: sqlite3.Connection,
) -> None:
    write_canonical(
        applied_conn,
        workspace_id="project-a",
        kind="decision",
        payload={
            "title": "Shadow vs real-paper edge delta on paired-cohort",
            "decision_text": "Track paired cohort delta before changing selector policy.",
            "rationale": "The compact brief must surface current decisions under budget.",
            "importance": 0.9,
        },
    )
    write_canonical(
        applied_conn,
        workspace_id="project-a",
        kind="behavior",
        payload={
            "name": "Very verbose research operator",
            "kind": "operating_rule",
            "scope": "workspace",
            "priority": "user_preference",
            "rule": " ".join(["long behavior text"] * 140),
            "confidence": 0.95,
        },
    )

    report = run_retrieval_quality_evals(
        applied_conn,
        workspace_id="project-a",
        cases=[
            RetrievalQualityCase(
                name="decision_budget",
                query="shadow versus real paper edge delta paired cohort",
                expected_sections=["decisions"],
                expected_object_titles=[
                    "Shadow vs real-paper edge delta on paired-cohort",
                ],
                min_render_level="summary",
                expected_omissions_absent=True,
                max_tokens=2500,
            )
        ],
    )

    assert report.status == "ok"
    assert report.results[0].render_levels["decisions"] == "summary"


def test_retrieval_quality_checks_object_titles_and_render_level(
    applied_conn: sqlite3.Connection,
) -> None:
    write_canonical(
        applied_conn,
        workspace_id="project-a",
        kind="decision",
        payload={
            "title": "Shadow vs real-paper edge delta on paired-cohort",
            "decision_text": "Paired cohort delta is positive enough to keep tracking.",
            "importance": 0.9,
        },
    )

    report = run_retrieval_quality_evals(
        applied_conn,
        workspace_id="project-a",
        cases=[
            RetrievalQualityCase(
                name="research_budget_stub",
                query="shadow versus real paper edge delta paired cohort experiment",
                expected_sections=["decisions"],
                expected_object_titles=[
                    "Shadow vs real-paper edge delta on paired-cohort",
                ],
                min_render_level="stub",
                expected_omissions_absent=True,
                max_tokens=2500,
            )
        ],
    )

    assert report.status == "ok"
    assert report.results[0].render_levels["decisions"] == "summary"
