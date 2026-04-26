from __future__ import annotations

import sqlite3

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


def _seed(conn: sqlite3.Connection) -> tuple[str, str, str]:
    project = upsert_entity(conn, EntityIn(type="project", canonical_name="agent-memory-lite"))
    sqlite_ent = upsert_entity(conn, EntityIn(type="tool", canonical_name="SQLite"))
    episode = insert_episode(
        conn,
        EpisodeIn(
            workspace_id="default",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="seed",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
    )
    return project.id, sqlite_ent.id, episode.id


def _fact(*, project: str, target: str, episode_id: str, text: str) -> FactIn:
    return FactIn(
        workspace_id="default",
        subject_entity_id=project,
        relation="USES",
        object_entity_id=target,
        fact_text=text,
        source_episode_id=episode_id,
        confidence=0.9,
        importance=0.7,
    )


def test_first_write_creates_open_fact(applied_conn: sqlite3.Connection) -> None:
    project, sqlite_id, episode_id = _seed(applied_conn)
    result = write_fact(
        applied_conn,
        _fact(project=project, target=sqlite_id, episode_id=episode_id, text="USES SQLite"),
    )
    assert result.fact.valid_to is None
    assert result.invalidated_ids == []
    actives = list_active_facts(applied_conn, "default")
    assert len(actives) == 1


def test_subsequent_write_invalidates_prior(applied_conn: sqlite3.Connection) -> None:
    project, sqlite_id, episode_id = _seed(applied_conn)
    neo4j = upsert_entity(applied_conn, EntityIn(type="tool", canonical_name="Neo4j"))
    first = write_fact(
        applied_conn,
        _fact(project=project, target=sqlite_id, episode_id=episode_id, text="USES SQLite"),
    )
    second = write_fact(
        applied_conn,
        _fact(project=project, target=neo4j.id, episode_id=episode_id, text="USES Neo4j"),
    )
    assert first.fact.id in second.invalidated_ids
    actives = list_active_facts(applied_conn, "default")
    assert {fact.id for fact in actives} == {second.fact.id}

    all_facts = list_all_facts_for_subjects(
        applied_conn,
        workspace_id="default",
        subject_ids=[project],
        include_invalidated=True,
    )
    closed = [f for f in all_facts if f.valid_to is not None]
    assert closed
    assert closed[0].invalidated_by_fact_id == second.fact.id


def test_workspace_isolation(applied_conn: sqlite3.Connection) -> None:
    project, sqlite_id, episode_id = _seed(applied_conn)
    other_project = upsert_entity(
        applied_conn,
        EntityIn(workspace_id="other", type="project", canonical_name="other-project"),
    )
    other_target = upsert_entity(
        applied_conn,
        EntityIn(workspace_id="other", type="tool", canonical_name="Postgres"),
    )
    other_episode = insert_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="other",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="seed-other",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
    )
    write_fact(
        applied_conn,
        _fact(project=project, target=sqlite_id, episode_id=episode_id, text="USES SQLite"),
    )
    write_fact(
        applied_conn,
        FactIn(
            workspace_id="other",
            subject_entity_id=other_project.id,
            relation="USES",
            object_entity_id=other_target.id,
            fact_text="USES Postgres",
            source_episode_id=other_episode.id,
        ),
    )
    default_actives = list_active_facts(applied_conn, "default")
    other_actives = list_active_facts(applied_conn, "other")
    assert {f.workspace_id for f in default_actives} == {"default"}
    assert {f.workspace_id for f in other_actives} == {"other"}
