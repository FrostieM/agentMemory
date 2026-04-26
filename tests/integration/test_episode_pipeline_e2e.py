"""Integration test for the episode write pipeline against a real tmp SQLite."""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.ingestion import episode_pipeline as pipeline_module
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn

pytestmark = pytest.mark.integration


def _episode(text: str = "agent observed test", **overrides: object) -> EpisodeIn:
    payload: dict[str, object] = {
        "workspace_id": "default",
        "task_id": "phase-1-validation",
        "source_type": EpisodeSource.AGENT_ACTION,
        "raw_text": text,
        "trust_level": TrustLevel.AGENT_OBSERVED,
        "importance": 0.7,
    }
    payload.update(overrides)
    return EpisodeIn(**payload)


def test_pipeline_persists_episode_chunk_and_fts(applied_conn: sqlite3.Connection) -> None:
    result = ingest_episode(applied_conn, _episode("Implemented retrieval module."))

    episode_row = applied_conn.execute(
        "SELECT id, raw_text FROM episodes WHERE id = ?", (result.episode.id,)
    ).fetchone()
    assert episode_row is not None
    assert episode_row["raw_text"] == "Implemented retrieval module."

    chunk_row = applied_conn.execute(
        "SELECT id, episode_id FROM chunks WHERE id = ?", (result.chunk.id,)
    ).fetchone()
    assert chunk_row is not None
    assert chunk_row["episode_id"] == result.episode.id

    hits = search_chunks_fts(applied_conn, workspace_id="default", query="retrieval", limit=10)
    assert any(hit.chunk_id == result.chunk.id for hit in hits)


def test_pipeline_redacts_before_storage(applied_conn: sqlite3.Connection) -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH"
    result = ingest_episode(applied_conn, _episode(f"deploy script: api_key={secret}"))

    stored = applied_conn.execute(
        "SELECT raw_text FROM episodes WHERE id = ?", (result.episode.id,)
    ).fetchone()
    assert secret not in stored["raw_text"]
    chunk_row = applied_conn.execute(
        "SELECT text FROM chunks WHERE id = ?", (result.chunk.id,)
    ).fetchone()
    assert secret not in chunk_row["text"]
    fts_row = applied_conn.execute(
        "SELECT text FROM chunks_fts WHERE chunk_id = ?", (result.chunk.id,)
    ).fetchone()
    assert secret not in fts_row["text"]
    assert any(kind in result.redacted_kinds for kind in ("openai_key", "api_key_kv"))


def test_pipeline_writes_audit_entry(applied_conn: sqlite3.Connection) -> None:
    result = ingest_episode(applied_conn, _episode("audit me"))
    audit_row = applied_conn.execute(
        "SELECT action, target_type, target_id FROM audit_log WHERE target_id = ?",
        (result.episode.id,),
    ).fetchone()
    assert audit_row["action"] == "ingest_episode"
    assert audit_row["target_type"] == "episode"


def test_pipeline_rolls_back_on_failure(
    applied_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated FTS failure")

    monkeypatch.setattr(pipeline_module, "insert_chunk_fts", explode)

    with pytest.raises(RuntimeError, match="simulated FTS failure"):
        ingest_episode(applied_conn, _episode("rollback test"))

    leaked = applied_conn.execute(
        "SELECT COUNT(*) FROM episodes WHERE task_id = 'phase-1-validation'"
    ).fetchone()
    assert leaked[0] == 0
