"""Write a decision, optionally superseding a prior one.

Closes the prior `valid_to` and flips its status to `superseded` inside the
same transaction as the new insert. Adds an audit row referencing the new
decision id.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.errors import NotFoundError, ValidationError
from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.ingestion.conflict_detect import detect_conflicts
from agent_memory_lite.models.decisions import Decision, DecisionIn
from agent_memory_lite.redaction.redactor import redact
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.decisions_repo import (
    close_decision,
    get_decision,
    insert_decision_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def write_decision(
    conn: sqlite3.Connection,
    payload: DecisionIn,
    *,
    settings: Settings | None = None,
) -> Decision:
    decision_id = new_id(IdKind.DECISION)
    timestamp = iso_now()

    if payload.supersedes_decision_id is not None:
        prior = get_decision(conn, payload.supersedes_decision_id)
        if prior is None:
            raise NotFoundError(
                f"supersedes_decision_id {payload.supersedes_decision_id!r} not found"
            )
        if prior.workspace_id != payload.workspace_id:
            raise ValidationError("supersedes_decision_id must belong to the same workspace")

    # v3.0.0-final: redaction runs on every text field before it reaches
    # SQLite (CLAUDE.md invariant: "Secrets never stored"). Operator-
    # pasted API keys / tokens / credentials in title / decision_text /
    # rationale are replaced with <<REDACTED:KIND>> markers BEFORE the
    # INSERT so the cleartext never lands on disk.
    title_safe = redact(payload.title).text
    decision_text_safe = redact(payload.decision_text).text
    rationale_safe = redact(payload.rationale).text if payload.rationale else None

    with with_tx(conn):
        if payload.supersedes_decision_id is not None:
            close_decision(
                conn,
                decision_id=payload.supersedes_decision_id,
                valid_to=timestamp,
            )
        insert_decision_row(
            conn,
            decision_id=decision_id,
            workspace_id=payload.workspace_id,
            title=title_safe,
            decision_text=decision_text_safe,
            rationale=rationale_safe,
            supersedes_decision_id=payload.supersedes_decision_id,
            source_episode_id=payload.source_episode_id,
            confidence=payload.confidence,
            importance=payload.importance,
            valid_from=timestamp,
            created_at=timestamp,
            references=payload.references,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="write_decision",
            target_type="decision",
            target_id=decision_id,
            source_episode_id=payload.source_episode_id,
            after={
                "title": title_safe,
                "supersedes": payload.supersedes_decision_id,
            },
        )

    decision = get_decision(conn, decision_id)
    assert decision is not None

    # Conflict detection runs AFTER the row commits so the new
    # decision is visible to the heuristic and the scan can compare
    # the full text. detect_conflicts is a no-op when the env flag
    # is off.
    detect_conflicts(
        conn,
        settings=settings if settings is not None else get_settings(),
        workspace_id=payload.workspace_id,
        target_type="decision",
        target_id=decision_id,
        target_text=" ".join(filter(None, [title_safe, decision_text_safe])),
    )
    return decision
