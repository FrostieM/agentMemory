"""Find-or-create entity with alias merging."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.graph.canonicalize import canonicalize_name
from agent_memory_lite.models.entities import Entity, EntityIn
from agent_memory_lite.redaction import redact
from agent_memory_lite.redaction.payload import redact_freetext_fields
from agent_memory_lite.repositories.entities_repo import (
    find_entity_by_name,
    insert_entity_row,
    update_entity_meta,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def upsert_entity(conn: sqlite3.Connection, payload: EntityIn) -> Entity:
    canonical = canonicalize_name(payload.canonical_name)
    if not canonical:
        raise ValueError("canonical_name cannot be empty after canonicalization")

    # round-D: aliases + properties were persisted cleartext (secrets could leak into
    # entities.aliases_json / properties_json). Redact them before any write. The
    # canonical_name stays the (already-canonicalized) lookup key.
    safe_aliases = [redact(a).text if isinstance(a, str) and a else a for a in payload.aliases]
    safe_props = (
        redact_freetext_fields(payload.properties) if payload.properties else payload.properties
    )

    existing = find_entity_by_name(
        conn,
        workspace_id=payload.workspace_id,
        entity_type=payload.type,
        canonical_name=canonical,
    )
    timestamp = iso_now()

    if existing is not None:
        merged_aliases = list(dict.fromkeys([*existing.aliases, *safe_aliases]))
        merged_props = {**existing.properties, **safe_props}
        if merged_aliases != existing.aliases or merged_props != existing.properties:
            update_entity_meta(
                conn,
                entity_id=existing.id,
                aliases=merged_aliases,
                properties=merged_props,
                timestamp=timestamp,
            )
        return Entity(
            id=existing.id,
            workspace_id=existing.workspace_id,
            type=existing.type,
            canonical_name=existing.canonical_name,
            aliases=merged_aliases,
            properties=merged_props,
            created_at=existing.created_at,
            updated_at=timestamp,
        )

    entity_id = new_id(IdKind.ENTITY)
    insert_entity_row(
        conn,
        entity_id=entity_id,
        workspace_id=payload.workspace_id,
        entity_type=payload.type,
        canonical_name=canonical,
        aliases=safe_aliases,
        properties=safe_props,
        timestamp=timestamp,
    )
    return Entity(
        id=entity_id,
        workspace_id=payload.workspace_id,
        type=payload.type,
        canonical_name=canonical,
        aliases=list(safe_aliases),
        properties=dict(safe_props),
        created_at=timestamp,
        updated_at=timestamp,
    )
