"""Property: traversal respects max_hops and max_facts bounds."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.graph.traversal import traverse_facts
from agent_memory_lite.graph.upsert_entity import upsert_entity
from agent_memory_lite.graph.write_fact import write_fact
from agent_memory_lite.models.entities import EntityIn
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.facts import FactIn
from agent_memory_lite.repositories.episodes_repo import insert_episode


def _build_chain(tmp: Path, length: int) -> tuple[str, str, list[str]]:
    conn = open_connection(tmp / "graph.db")
    apply_migrations(conn, MIGRATION_DIR)
    head = upsert_entity(conn, EntityIn(type="node", canonical_name="head"))
    nodes = [head.id]
    for i in range(length):
        nxt = upsert_entity(conn, EntityIn(type="node", canonical_name=f"n{i}"))
        nodes.append(nxt.id)
    episode = insert_episode(
        conn,
        EpisodeIn(
            workspace_id="default",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="seed",
            trust_level=TrustLevel.AGENT_OBSERVED,
        ),
    )
    for src, dst in pairwise(nodes):
        write_fact(
            conn,
            FactIn(
                subject_entity_id=src,
                relation="LINK",
                object_entity_id=dst,
                fact_text=f"{src}->{dst}",
                source_episode_id=episode.id,
            ),
        )
    conn.close()
    return head.id, str(tmp / "graph.db"), nodes


@given(
    chain_length=st.integers(min_value=2, max_value=6),
    max_hops=st.integers(min_value=1, max_value=4),
    max_facts=st.integers(min_value=1, max_value=20),
)
@settings(max_examples=30, deadline=None)
def test_traversal_respects_bounds(
    tmp_path_factory: pytest.TempPathFactory,
    chain_length: int,
    max_hops: int,
    max_facts: int,
) -> None:
    tmp = tmp_path_factory.mktemp("traverse")
    head_id, db_path, _ = _build_chain(tmp, chain_length)

    conn = open_connection(db_path)
    try:
        hits = traverse_facts(
            conn,
            workspace_id="default",
            seeds=[head_id],
            max_hops=max_hops,
            max_facts=max_facts,
        )
        assert len(hits) <= max_facts
        assert all(1 <= hit.hop <= max_hops for hit in hits)
    finally:
        close_connection(conn)
