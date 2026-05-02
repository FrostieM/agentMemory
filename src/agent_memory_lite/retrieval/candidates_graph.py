"""Graph candidate fetcher for the retrieval pipeline.

Phase 4 keeps seed selection simple: lower-cased query tokens are matched
against canonicalized entity names. Returned candidates carry the fact text
and metadata so the context builder can render them as `<retrieved_facts>`.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.graph.canonicalize import canonicalize_name
from agent_memory_lite.graph.traversal import GraphHit, traverse_facts
from agent_memory_lite.models.retrieval import RetrievalCandidate
from agent_memory_lite.repositories.entities_repo import list_entities

DEFAULT_LIMIT = 40


def _seeds_from_query(conn: sqlite3.Connection, *, workspace_id: str, query: str) -> list[str]:
    raw_tokens = {tok for tok in canonicalize_name(query).split() if tok}
    if not raw_tokens:
        return []
    seeds: list[str] = []
    for entity in list_entities(conn, workspace_id):
        if entity.canonical_name in raw_tokens or any(
            canonicalize_name(alias) in raw_tokens for alias in entity.aliases
        ):
            seeds.append(entity.id)
    return seeds


def _hit_to_candidate(hit: GraphHit) -> RetrievalCandidate:
    fact = hit.fact
    return RetrievalCandidate(
        id=fact.id,
        workspace_id=fact.workspace_id,
        source="graph",
        text=fact.fact_text,
        path="",
        summary=None,
        raw_score=fact.confidence,
        metadata={
            "relation": fact.relation,
            "subject_entity_id": fact.subject_entity_id,
            "object_entity_id": fact.object_entity_id,
            "literal_value": fact.literal_value,
            "valid_from": fact.valid_from,
            "valid_to": fact.valid_to,
            "trust_level": fact.trust_level.value,
            "hop": hit.hop,
        },
    )


def collect_graph(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str,
    limit: int = DEFAULT_LIMIT,
    historical: bool = False,
) -> list[RetrievalCandidate]:
    seeds = _seeds_from_query(conn, workspace_id=workspace_id, query=query)
    if not seeds:
        return []
    hits = traverse_facts(
        conn,
        workspace_id=workspace_id,
        seeds=seeds,
        max_facts=limit,
        historical=historical,
    )
    return [_hit_to_candidate(hit) for hit in hits]
