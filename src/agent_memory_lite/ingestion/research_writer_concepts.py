"""Upsert reusable domain concepts."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.research import DomainConcept, DomainConceptIn
from agent_memory_lite.redaction.redactor import redact
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.research_repo import (
    get_concept_by_name,
    upsert_concept_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

_log = get_logger("ingestion.research_writer_concepts")


def upsert_domain_concept(conn: sqlite3.Connection, payload: DomainConceptIn) -> DomainConcept:
    concept_id = new_id(IdKind.DOMAIN_CONCEPT)
    timestamp = iso_now()
    # Redact secret shapes from EVERY free-text field BEFORE the row hits SQLite +
    # the durable_fts index (CLAUDE.md: "Never store secrets"). This direct route
    # (POST /memory/upsert_concept + seeding) bypasses write_canonical, so it must
    # mirror redact_freetext_fields itself -- which redacts name/definition/aliases
    # AND tags. redact() is identity on non-secret text.
    # ``name`` is FTS-indexed (_FTS_COLUMNS['concept'] starts with name) AND the
    # (workspace_id, name) upsert key, so the redacted value is used CONSISTENTLY for
    # the insert + every get_concept_by_name below. Accepted trade-off (matches the
    # write_canonical path): redact() maps every secret to one marker, so two names
    # differing ONLY by a secret would collapse to one row. A secret in a NAME is
    # abuse, and the alternative (cleartext name, now FTS-indexed) is the worse harm.
    name_safe = redact(payload.name).text
    definition_safe = redact(payload.definition).text
    aliases_safe = [redact(alias).text for alias in payload.aliases]
    tags_safe = [redact(tag).text for tag in payload.tags]
    with with_tx(conn):
        upsert_concept_row(
            conn,
            concept_id=concept_id,
            workspace_id=payload.workspace_id,
            name=name_safe,
            kind=payload.kind,
            definition=definition_safe,
            aliases=aliases_safe,
            tags=tags_safe,
            source_episode_id=payload.source_episode_id,
            confidence=payload.confidence,
            active=payload.active,
            created_at=timestamp,
            updated_at=timestamp,
        )
        stored = get_concept_by_name(conn, workspace_id=payload.workspace_id, name=name_safe)
        assert stored is not None
        # M1 (write-atomicity batch): this path (research-taxonomy HTTP route +
        # project seeding) upserts the concept directly, bypassing write_canonical's
        # FTS sync choke point. concept is a DURABLE_FTS_KIND (name + definition are
        # indexed), so without this sync the concept is invisible to memory_search.
        # Sync ``stored.id`` -- on a name conflict upsert_concept_row REUSES the
        # existing row id, so the generated ``concept_id`` is NOT the row's id.
        # Inside the with_tx so a rolled-back upsert takes the index entry with it.
        from agent_memory_lite.fts.durable_fts import sync_durable_fts  # noqa: PLC0415

        if not sync_durable_fts(
            conn, kind="concept", object_id=stored.id, workspace_id=payload.workspace_id
        ):
            _log.warning(
                "durable_fts_sync_skipped_on_upsert_concept",
                object_id=stored.id,
                workspace_id=payload.workspace_id,
            )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="upsert_domain_concept",
            target_type="domain_concept",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={"name": name_safe, "kind": payload.kind.value, "active": payload.active},
        )
    concept = get_concept_by_name(conn, workspace_id=payload.workspace_id, name=name_safe)
    assert concept is not None
    return concept
