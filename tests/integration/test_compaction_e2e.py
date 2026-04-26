"""Integration: compaction summarization + stale fact archival."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from agent_memory_lite.compaction.invalidate_stale import archive_stale_facts
from agent_memory_lite.compaction.summarize_old import summarize_old_episodes
from agent_memory_lite.graph.upsert_entity import upsert_entity
from agent_memory_lite.graph.write_fact import write_fact
from agent_memory_lite.models.entities import EntityIn
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.facts import FactIn
from agent_memory_lite.repositories.episodes_repo import insert_episode
from agent_memory_lite.utils.time import reset_now_provider, set_now_provider

pytestmark = pytest.mark.integration


def test_summary_episode_records_count(applied_conn: sqlite3.Connection) -> None:
    fixed = datetime(2025, 1, 1, tzinfo=UTC)
    set_now_provider(lambda: fixed)
    try:
        for i in range(5):
            insert_episode(
                applied_conn,
                EpisodeIn(
                    workspace_id="default",
                    source_type=EpisodeSource.AGENT_ACTION,
                    raw_text=f"event {i}",
                    trust_level=TrustLevel.AGENT_OBSERVED,
                ),
            )
    finally:
        reset_now_provider()
    set_now_provider(lambda: fixed + timedelta(days=120))
    try:
        stats = summarize_old_episodes(applied_conn, workspace_id="default", age_days=30)
    finally:
        reset_now_provider()
    assert stats.summarized_episodes == 5


def test_archive_stale_facts_marks_metadata(applied_conn: sqlite3.Connection) -> None:
    fixed = datetime(2025, 1, 1, tzinfo=UTC)
    set_now_provider(lambda: fixed)
    project = upsert_entity(applied_conn, EntityIn(type="project", canonical_name="P"))
    sqlite_ent = upsert_entity(applied_conn, EntityIn(type="tool", canonical_name="SQLite"))
    other = upsert_entity(applied_conn, EntityIn(type="tool", canonical_name="Postgres"))
    episode = insert_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="default",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="seed",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
    )
    write_fact(
        applied_conn,
        FactIn(
            subject_entity_id=project.id,
            relation="USES",
            object_entity_id=sqlite_ent.id,
            fact_text="USES SQLite",
            source_episode_id=episode.id,
        ),
    )
    write_fact(
        applied_conn,
        FactIn(
            subject_entity_id=project.id,
            relation="USES",
            object_entity_id=other.id,
            fact_text="USES Postgres",
            source_episode_id=episode.id,
        ),
    )
    reset_now_provider()
    set_now_provider(lambda: fixed + timedelta(days=400))
    try:
        stats = archive_stale_facts(applied_conn, workspace_id="default", max_age_days=60)
    finally:
        reset_now_provider()

    assert stats.stale_count >= 1
    rows = applied_conn.execute(
        "SELECT metadata_json FROM facts WHERE valid_to IS NOT NULL"
    ).fetchall()
    assert any(json.loads(row["metadata_json"] or "{}").get("stale") for row in rows)
