from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.maintenance.encoding_audit import run_encoding_audit
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.repositories.chunks_repo import EMBEDDING_STALE_REASON_KEY


def _mojibake(text: str) -> str:
    return text.encode("utf-8").decode("cp1251")


def test_encoding_audit_repairs_stored_mojibake(applied_conn: sqlite3.Connection) -> None:
    expected = "\u041f\u0440\u0438\u0432\u0435\u0442 memory"
    decision = write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="project-a",
            title="Bad text",
            decision_text=_mojibake(expected),
            rationale="Reason",
        ),
    )

    report = run_encoding_audit(applied_conn, workspace_id="project-a")

    assert report.status == "warning"
    assert any(finding.row_id == decision.id for finding in report.findings)

    repaired = run_encoding_audit(applied_conn, workspace_id="project-a", repair=True)
    row = applied_conn.execute(
        "SELECT decision_text FROM decisions WHERE id = ?", (decision.id,)
    ).fetchone()

    assert repaired.repaired_cells == 1
    assert row["decision_text"] == expected


def test_encoding_repair_marks_repaired_chunk_embedding_stale(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    expected = "\u041f\u0440\u0438\u0432\u0435\u0442 vector"
    result = ingest_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="project-a",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text=_mojibake(expected),
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )
    assert result.chunk.embedding_id == result.chunk.id

    repaired = run_encoding_audit(applied_conn, workspace_id="project-a", repair=True)
    row = applied_conn.execute(
        "SELECT text, embedding_id, metadata_json FROM chunks WHERE id = ?",
        (result.chunk.id,),
    ).fetchone()

    assert repaired.repaired_cells >= 1
    assert row["text"] == expected
    metadata = json.loads(row["metadata_json"])
    assert row["embedding_id"] == result.chunk.id
    assert metadata[EMBEDDING_STALE_REASON_KEY] == "encoding_repair_changed_chunk_text"
