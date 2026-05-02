"""Property: at most one open fact per (subject, relation), no cycles."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.graph.upsert_entity import upsert_entity
from agent_memory_lite.graph.write_fact import write_fact
from agent_memory_lite.models.entities import EntityIn
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.facts import FactIn
from agent_memory_lite.repositories.episodes_repo import insert_episode
from agent_memory_lite.repositories.facts_repo import (
    list_active_facts,
    list_all_facts_for_subjects,
)


@pytest.fixture
def fresh_graph(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_connection(tmp_path / "graph.db")
    apply_migrations(conn, MIGRATION_DIR)
    try:
        yield conn
    finally:
        close_connection(conn)


def _seed(conn: sqlite3.Connection, count: int) -> tuple[str, list[str], str]:
    subject = upsert_entity(conn, EntityIn(type="proj", canonical_name="P"))
    targets = [
        upsert_entity(conn, EntityIn(type="tool", canonical_name=f"T{i}")) for i in range(count)
    ]
    episode = insert_episode(
        conn,
        EpisodeIn(
            workspace_id="default",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="seed",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
    )
    return subject.id, [t.id for t in targets], episode.id


@given(target_count=st.integers(min_value=2, max_value=6))
@settings(max_examples=20, deadline=None)
def test_only_latest_fact_remains_open(
    tmp_path_factory: pytest.TempPathFactory, target_count: int
) -> None:
    tmp = tmp_path_factory.mktemp("conflicts")
    conn = open_connection(tmp / "graph.db")
    apply_migrations(conn, MIGRATION_DIR)
    try:
        subject_id, target_ids, episode_id = _seed(conn, target_count)
        latest_id: str | None = None
        for target in target_ids:
            result = write_fact(
                conn,
                FactIn(
                    subject_entity_id=subject_id,
                    relation="USES",
                    object_entity_id=target,
                    fact_text=f"USES {target}",
                    source_episode_id=episode_id,
                ),
            )
            latest_id = result.fact.id

        opens = list_active_facts(conn, "default")
        assert len(opens) == 1
        assert opens[0].id == latest_id

        all_facts = list_all_facts_for_subjects(
            conn,
            workspace_id="default",
            subject_ids=[subject_id],
            include_invalidated=True,
        )
        closed = [f for f in all_facts if f.valid_to is not None]
        fact_index = {f.id: f for f in all_facts}
        for fact in closed:
            assert fact.invalidated_by_fact_id is not None
            assert fact.invalidated_by_fact_id != fact.id
            cursor = fact.invalidated_by_fact_id
            visited = {fact.id}
            while cursor is not None and cursor in fact_index:
                assert cursor not in visited, "invalidation chain cycles"
                visited.add(cursor)
                cursor = fact_index[cursor].invalidated_by_fact_id
            assert latest_id in visited
    finally:
        close_connection(conn)
