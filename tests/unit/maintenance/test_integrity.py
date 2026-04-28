from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.ingestion.capability_writer import upsert_agent_skill
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.theory_writer import write_theory
from agent_memory_lite.maintenance.integrity import repair_fts, run_integrity_audit
from agent_memory_lite.models.capabilities import AgentSkillIn
from agent_memory_lite.models.capability_links import CapabilityLinkIn
from agent_memory_lite.models.enums import (
    CapabilityLinkRelation,
    CapabilityLinkTargetType,
    CapabilityType,
    EpisodeSource,
    TrustLevel,
)
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.theories import TheoryIn


def _episode(text: str, *, workspace_id: str = "project-a") -> EpisodeIn:
    return EpisodeIn(
        workspace_id=workspace_id,
        source_type=EpisodeSource.AGENT_ACTION,
        raw_text=text,
        trust_level=TrustLevel.AGENT_OBSERVED,
    )


def test_integrity_detects_fts_workspace_mismatch(applied_conn: sqlite3.Connection) -> None:
    result = ingest_episode(applied_conn, _episode("retrieval parity control token"))
    applied_conn.execute(
        "UPDATE chunks_fts SET workspace_id = 'default' WHERE chunk_id = ?",
        (result.chunk.id,),
    )

    report = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert report.status == "degraded"
    assert report.checks["fts"].details["workspace_mismatch"] == 1
    assert "fts" in report.failures


def test_repair_fts_restores_parity(applied_conn: sqlite3.Connection) -> None:
    result = ingest_episode(applied_conn, _episode("repair parity control token"))
    applied_conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (result.chunk.id,))

    before = run_integrity_audit(applied_conn, workspace_id="project-a")
    assert before.checks["fts"].status == "degraded"

    inserted = repair_fts(applied_conn, workspace_id="project-a")
    after = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert inserted == 1
    assert after.checks["fts"].status == "ok"


def test_integrity_detects_vector_missing(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    ingest_episode(
        applied_conn,
        _episode("vector parity control token"),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )
    fake_vector_store.drop_namespace("chunks")

    report = run_integrity_audit(
        applied_conn,
        workspace_id="project-a",
        vector_store=fake_vector_store,
    )

    assert report.status == "degraded"
    assert report.checks["vector"].details["missing"] == 1
    assert "vector" in report.failures


def test_integrity_detects_default_workspace_pollution(
    applied_conn: sqlite3.Connection,
) -> None:
    ingest_episode(applied_conn, _episode("default pollution", workspace_id="default"))

    report = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert report.status == "degraded"
    assert report.checks["workspace_pollution"].details["default_rows"]


def test_integrity_detects_dangling_capability_link(
    applied_conn: sqlite3.Connection,
) -> None:
    skill = upsert_agent_skill(
        applied_conn,
        AgentSkillIn(
            workspace_id="project-a",
            name="Replay and backtest design",
            summary="Validate hypotheses with replay.",
        ),
    )
    theory = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="project-a",
            title="Sparse opens are a learning bottleneck",
            claim="Admission policy may be too sparse to learn.",
        ),
    )
    link_capability(
        applied_conn,
        CapabilityLinkIn(
            workspace_id="project-a",
            target_type=CapabilityLinkTargetType.THEORY,
            target_id=theory.id,
            capability_type=CapabilityType.SKILL,
            capability_id=skill.id,
            relation=CapabilityLinkRelation.METHOD,
        ),
    )

    before = run_integrity_audit(applied_conn, workspace_id="project-a")
    assert before.checks["capability_links"].status == "ok"

    applied_conn.execute("DELETE FROM agent_skills WHERE id = ?", (skill.id,))
    after = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert after.status == "degraded"
    assert after.checks["capability_links"].details["missing_capabilities"]["skill"] == 1
