"""Write a temporal fact, closing any conflicting prior facts.

The whole flow runs inside one SQLite transaction so a partially-written fact
+ partially-closed conflicts cannot persist on failure.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.graph.conflict_detector import find_conflicting_facts
from agent_memory_lite.graph.invalidate import invalidate_facts
from agent_memory_lite.models.facts import Fact, FactIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.facts_repo import get_fact, insert_fact_row
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class FactWriteResult:
    fact: Fact
    invalidated_ids: list[str]


def write_fact(conn: sqlite3.Connection, payload: FactIn) -> FactWriteResult:
    timestamp = iso_now()
    fact_id = new_id(IdKind.FACT)

    conflicts = find_conflicting_facts(
        conn,
        workspace_id=payload.workspace_id,
        subject_entity_id=payload.subject_entity_id,
        relation=payload.relation,
    )
    conflict_ids = [c.id for c in conflicts]

    with with_tx(conn):
        insert_fact_row(
            conn,
            fact_id=fact_id,
            workspace_id=payload.workspace_id,
            subject_entity_id=payload.subject_entity_id,
            relation=payload.relation,
            object_entity_id=payload.object_entity_id,
            literal_value=payload.literal_value,
            fact_text=payload.fact_text,
            source_episode_id=payload.source_episode_id,
            confidence=payload.confidence,
            importance=payload.importance,
            trust_level=payload.trust_level,
            observed_at=timestamp,
            valid_from=timestamp,
            metadata=payload.metadata,
            created_at=timestamp,
        )
        invalidate_facts(
            conn,
            fact_ids=conflict_ids,
            valid_to=timestamp,
            invalidated_by_fact_id=fact_id,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="write_fact",
            target_type="fact",
            target_id=fact_id,
            source_episode_id=payload.source_episode_id,
            after={
                "relation": payload.relation,
                "subject": payload.subject_entity_id,
                "invalidated": conflict_ids,
            },
        )

    fact = get_fact(conn, fact_id)
    assert fact is not None
    return FactWriteResult(fact=fact, invalidated_ids=conflict_ids)
