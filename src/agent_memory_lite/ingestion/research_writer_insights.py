"""Distill + update research insights."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.errors import NotFoundError
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.ingestion.research_writer_shared import validate_workspace
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.research import (
    ResearchInsight,
    ResearchInsightIn,
    ResearchInsightUpdateIn,
)
from agent_memory_lite.redaction.redactor import redact
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.research_repo import (
    get_insight,
    insert_insight_row,
    update_insight_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

_log = get_logger("ingestion.research_writer_insights")


def distill_insight(conn: sqlite3.Connection, payload: ResearchInsightIn) -> ResearchInsight:
    insight_id = new_id(IdKind.RESEARCH_INSIGHT)
    timestamp = iso_now()
    # Redact secret shapes from free-text BEFORE the row hits SQLite + durable_fts
    # (CLAUDE.md: "Never store secrets"). This writer (reached via
    # POST /memory/distill_insight + candidate promotion) previously did NOT
    # redact, so a pasted secret landed cleartext on disk and -- once this path
    # syncs durable_fts -- would surface in memory_search. The blessed memory_write
    # path redacts in write_canonical; this direct route must too. redact() is
    # identity on non-secret text; summary + proposed_action are FTS-indexed, and
    # ``tags`` is redacted too (mirrors redact_freetext_fields) so a secret in a tag
    # cannot land cleartext in insights.tags_json and surface via memory_get.
    summary_safe = redact(payload.summary).text
    proposed_action_safe = (
        redact(payload.proposed_action).text if payload.proposed_action else payload.proposed_action
    )
    tags_safe = [redact(tag).text for tag in payload.tags]
    with with_tx(conn):
        insert_insight_row(
            conn,
            insight_id=insight_id,
            workspace_id=payload.workspace_id,
            insight_type=payload.insight_type,
            summary=summary_safe,
            proposed_action=proposed_action_safe,
            target_type=payload.target_type,
            target_id=payload.target_id,
            source_episode_ids=payload.source_episode_ids,
            confidence=payload.confidence,
            status=payload.status,
            tags=tags_safe,
            created_at=timestamp,
        )
        # M1 (write-atomicity batch): insert_insight_row INSERTs directly,
        # bypassing write_canonical's FTS sync choke point, so this distill path
        # owns the durable_fts sync. insight is a DURABLE_FTS_KIND -- without it
        # the distilled insight is invisible to memory_search until the brain-pass
        # rebuild backstop re-indexes it. Inside the with_tx so a rolled-back
        # distill takes the index entry with it; failure-soft + observed.
        from agent_memory_lite.fts.durable_fts import sync_durable_fts  # noqa: PLC0415

        if not sync_durable_fts(
            conn, kind="insight", object_id=insight_id, workspace_id=payload.workspace_id
        ):
            _log.warning(
                "durable_fts_sync_skipped_on_distill_insight",
                object_id=insight_id,
                workspace_id=payload.workspace_id,
            )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="distill_insight",
            target_type="research_insight",
            target_id=insight_id,
            source_episode_id=payload.source_episode_ids[0] if payload.source_episode_ids else None,
            after={
                "insight_type": payload.insight_type.value,
                "target_type": payload.target_type,
                "target_id": payload.target_id,
            },
        )
    insight = get_insight(conn, insight_id)
    assert insight is not None
    return insight


def update_insight(conn: sqlite3.Connection, payload: ResearchInsightUpdateIn) -> ResearchInsight:
    insight = get_insight(conn, payload.insight_id)
    if insight is None:
        raise NotFoundError(f"insight_id {payload.insight_id!r} not found")
    validate_workspace(
        item_workspace_id=insight.workspace_id,
        payload_workspace_id=payload.workspace_id,
        field_name="insight_id",
    )

    timestamp = iso_now()
    with with_tx(conn):
        update_insight_row(
            conn,
            insight_id=payload.insight_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            status=payload.status,
            updated_at=timestamp,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="update_insight",
            target_type="research_insight",
            target_id=payload.insight_id,
            source_episode_id=payload.source_episode_id,
            before={
                "target_type": insight.target_type,
                "target_id": insight.target_id,
                "status": insight.status.value,
            },
            after={
                "target_type": payload.target_type or insight.target_type,
                "target_id": payload.target_id or insight.target_id,
                "status": (payload.status or insight.status).value,
            },
        )
    updated = get_insight(conn, payload.insight_id)
    assert updated is not None
    return updated
