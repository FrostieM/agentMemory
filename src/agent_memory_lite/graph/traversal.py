"""Bounded breadth-first graph traversal.

Starts from a list of seed entity ids, follows facts up to `max_hops`, and
returns at most `max_facts` hits. Default search hides invalidated facts;
historical mode includes them.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.models.facts import Fact
from agent_memory_lite.repositories.facts_repo import list_all_facts_for_subjects

DEFAULT_MAX_HOPS = 2
DEFAULT_MAX_FACTS = 40


@dataclass(frozen=True, slots=True)
class GraphHit:
    fact: Fact
    hop: int


def traverse_facts(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    seeds: list[str],
    max_hops: int = DEFAULT_MAX_HOPS,
    max_facts: int = DEFAULT_MAX_FACTS,
    historical: bool = False,
) -> list[GraphHit]:
    if not seeds or max_hops <= 0:
        return []

    visited_entities: set[str] = set(seeds)
    frontier: list[str] = list(seeds)
    hits: list[GraphHit] = []
    seen_facts: set[str] = set()

    for hop in range(1, max_hops + 1):
        if not frontier or len(hits) >= max_facts:
            break
        facts = list_all_facts_for_subjects(
            conn,
            workspace_id=workspace_id,
            subject_ids=frontier,
            include_invalidated=historical,
        )
        next_frontier: list[str] = []
        for fact in facts:
            if fact.id in seen_facts:
                continue
            seen_facts.add(fact.id)
            hits.append(GraphHit(fact=fact, hop=hop))
            if len(hits) >= max_facts:
                break
            target = fact.object_entity_id
            if target is not None and target not in visited_entities:
                visited_entities.add(target)
                next_frontier.append(target)
        frontier = next_frontier

    return hits
