from __future__ import annotations

import sqlite3

from agent_memory_lite.graph.traversal import traverse_facts
from agent_memory_lite.graph.upsert_entity import upsert_entity
from agent_memory_lite.graph.write_fact import write_fact
from agent_memory_lite.models.entities import EntityIn
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.facts import FactIn
from agent_memory_lite.repositories.episodes_repo import insert_episode


def _seed_chain(conn: sqlite3.Connection) -> tuple[str, str, str, str]:
    project = upsert_entity(conn, EntityIn(type="project", canonical_name="memory-lite"))
    sqlite_ent = upsert_entity(conn, EntityIn(type="tool", canonical_name="SQLite"))
    fts5 = upsert_entity(conn, EntityIn(type="feature", canonical_name="FTS5"))
    episode = insert_episode(
        conn,
        EpisodeIn(
            workspace_id="default",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="seed",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
    )
    write_fact(
        conn,
        FactIn(
            subject_entity_id=project.id,
            relation="USES",
            object_entity_id=sqlite_ent.id,
            fact_text="memory-lite USES SQLite",
            source_episode_id=episode.id,
        ),
    )
    write_fact(
        conn,
        FactIn(
            subject_entity_id=sqlite_ent.id,
            relation="OFFERS",
            object_entity_id=fts5.id,
            fact_text="SQLite OFFERS FTS5",
            source_episode_id=episode.id,
        ),
    )
    return project.id, sqlite_ent.id, fts5.id, episode.id


def test_traversal_one_hop(applied_conn: sqlite3.Connection) -> None:
    project, _sqlite, _fts5, _ep = _seed_chain(applied_conn)
    hits = traverse_facts(
        applied_conn,
        workspace_id="default",
        seeds=[project],
        max_hops=1,
    )
    assert len(hits) == 1
    assert hits[0].hop == 1


def test_traversal_two_hops(applied_conn: sqlite3.Connection) -> None:
    project, sqlite_id, fts5, _ep = _seed_chain(applied_conn)
    hits = traverse_facts(
        applied_conn,
        workspace_id="default",
        seeds=[project],
        max_hops=2,
    )
    relations = {hit.fact.relation for hit in hits}
    assert {"USES", "OFFERS"} <= relations
    target_ids = {hit.fact.object_entity_id for hit in hits}
    assert sqlite_id in target_ids
    assert fts5 in target_ids


def test_traversal_respects_max_facts(applied_conn: sqlite3.Connection) -> None:
    project, _, _, _ = _seed_chain(applied_conn)
    hits = traverse_facts(
        applied_conn,
        workspace_id="default",
        seeds=[project],
        max_hops=2,
        max_facts=1,
    )
    assert len(hits) == 1


def test_traversal_zero_hops_returns_empty(applied_conn: sqlite3.Connection) -> None:
    project, _, _, _ = _seed_chain(applied_conn)
    hits = traverse_facts(
        applied_conn,
        workspace_id="default",
        seeds=[project],
        max_hops=0,
    )
    assert hits == []


def test_traversal_respects_workspace(applied_conn: sqlite3.Connection) -> None:
    project, _, _, _ = _seed_chain(applied_conn)
    hits = traverse_facts(
        applied_conn,
        workspace_id="other",
        seeds=[project],
        max_hops=2,
    )
    assert hits == []
