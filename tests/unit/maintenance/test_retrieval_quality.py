from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.capability_writer import upsert_agent_role
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.research_writer import write_experiment
from agent_memory_lite.ingestion.theory_writer import write_theory
from agent_memory_lite.maintenance.retrieval_quality import (
    RetrievalQualityCase,
    run_retrieval_quality_evals,
)
from agent_memory_lite.models.capabilities import AgentRoleIn
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.research import ExperimentIn
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.models.theories import TheoryIn
from agent_memory_lite.retrieval.context_builder import build_context


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
                expected_sources=["fts", "vector"],
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


def test_context_budget_prefers_relevant_research_agenda_over_capability_block(
    applied_conn: sqlite3.Connection,
) -> None:
    write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="project-a",
            title="Shadow bias theory",
            claim="Shadow can overstate real paper edge.",
            validation_criteria=["paired cohort delta"],
            experiment_plan="Run paired cohort replay.",
            importance=0.9,
        ),
    )
    write_experiment(
        applied_conn,
        ExperimentIn(
            workspace_id="project-a",
            title="Shadow vs real-paper edge delta on paired-cohort",
            hypothesis="Paired cohort delta is positive.",
            cohort_definition="wallets with paired shadow and paper closes",
            success_criteria={"min_wallets": 30},
            priority=0.9,
        ),
    )
    upsert_agent_role(
        applied_conn,
        AgentRoleIn(
            workspace_id="project-a",
            name="Very verbose research operator",
            purpose=" ".join(["long capability text"] * 140),
            confidence=0.95,
        ),
    )

    built = build_context(
        applied_conn,
        RetrievalQuery(
            workspace_id="project-a",
            query="shadow versus real paper edge delta paired cohort experiment",
            max_tokens=2500,
        ),
    )

    assert "Shadow vs real-paper edge delta on paired-cohort" in built.text
    assert "<research_agenda" in built.text
    assert built.budget_diagnostics["intent"] == ["research"]
    research_section = {item["name"]: item for item in built.budget_diagnostics["sections"]}[
        "research_agenda"
    ]
    assert research_section["render_level"] in {"stub", "summary", "full"}
    assert research_section["objects_included"] >= 1


def test_retrieval_quality_checks_object_titles_and_render_level(
    applied_conn: sqlite3.Connection,
) -> None:
    write_experiment(
        applied_conn,
        ExperimentIn(
            workspace_id="project-a",
            title="Shadow vs real-paper edge delta on paired-cohort",
            hypothesis="Paired cohort delta is positive.",
            cohort_definition="wallets with paired shadow and paper closes",
            success_criteria={"min_wallets": 30},
            priority=0.9,
        ),
    )

    report = run_retrieval_quality_evals(
        applied_conn,
        workspace_id="project-a",
        cases=[
            RetrievalQualityCase(
                name="research_budget_stub",
                query="shadow versus real paper edge delta paired cohort experiment",
                expected_sections=["research_agenda"],
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
    assert report.results[0].render_levels["research_agenda"] in {"stub", "summary", "full"}
