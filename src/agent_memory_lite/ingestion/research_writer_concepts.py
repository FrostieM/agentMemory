"""Upsert reusable domain concepts."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.research import DomainConcept, DomainConceptIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.research_repo import (
    get_concept_by_name,
    upsert_concept_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def upsert_domain_concept(conn: sqlite3.Connection, payload: DomainConceptIn) -> DomainConcept:
    concept_id = new_id(IdKind.DOMAIN_CONCEPT)
    timestamp = iso_now()
    with with_tx(conn):
        upsert_concept_row(
            conn,
            concept_id=concept_id,
            workspace_id=payload.workspace_id,
            name=payload.name,
            kind=payload.kind,
            definition=payload.definition,
            aliases=payload.aliases,
            tags=payload.tags,
            source_episode_id=payload.source_episode_id,
            confidence=payload.confidence,
            active=payload.active,
            created_at=timestamp,
            updated_at=timestamp,
        )
        stored = get_concept_by_name(conn, workspace_id=payload.workspace_id, name=payload.name)
        assert stored is not None
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="upsert_domain_concept",
            target_type="domain_concept",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={"name": payload.name, "kind": payload.kind.value, "active": payload.active},
        )
    concept = get_concept_by_name(conn, workspace_id=payload.workspace_id, name=payload.name)
    assert concept is not None
    return concept
