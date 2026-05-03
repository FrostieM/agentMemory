"""Dedup-and-increment for hygiene findings -> maintenance_events (v1.9).

When the hygiene report runs repeatedly, the same "stale candidate" finding
should not generate a fresh maintenance_event each pass. Instead, we look up
an existing OPEN event for the same (kind, target_type, target_id) and
increment its recurrence_count + last_seen_at. The first time a recurrence
crosses ``recurrence_threshold`` we emit a ``maintenance.recurrence_
threshold_crossed`` audit row so the operator sees the inflection.

Resolved/ignored events are not reused — closing an event means "I looked
at this"; if the same finding shows up again it should produce a fresh
event so the operator re-evaluates.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class RecurrenceUpsertResult:
    event_id: str
    is_new: bool
    recurrence_count: int
    crossed_threshold: bool


def _find_open_event_id(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    target_type: str | None,
    target_id: str | None,
) -> tuple[str, int] | None:
    row = conn.execute(
        """
        SELECT id, recurrence_count FROM maintenance_events
        WHERE workspace_id = ?
          AND kind = ?
          AND COALESCE(target_type, '') = COALESCE(?, '')
          AND COALESCE(target_id, '') = COALESCE(?, '')
          AND status = 'open'
        ORDER BY last_seen_at DESC NULLS LAST, created_at DESC
        LIMIT 1
        """,
        (workspace_id, kind, target_type, target_id),
    ).fetchone()
    if row is None:
        return None
    return (str(row["id"]), int(row["recurrence_count"] or 1))


def upsert_finding_event(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    severity: str,
    summary: str,
    details: dict[str, Any],
    target_type: str | None,
    target_id: str | None,
    threshold: int,
) -> RecurrenceUpsertResult:
    """Insert OR increment an open maintenance_event for the finding."""
    now_iso = iso_now()
    existing = _find_open_event_id(
        conn,
        workspace_id=workspace_id,
        kind=kind,
        target_type=target_type,
        target_id=target_id,
    )
    if existing is None:
        event_id = new_id(IdKind.MAINTENANCE_EVENT)
        conn.execute(
            """
            INSERT INTO maintenance_events
            (id, workspace_id, kind, severity, status, summary, details_json,
             source_episode_id, target_type, target_id, created_at,
             resolved_at, recurrence_count, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, 'open', ?, ?, NULL, ?, ?, ?, NULL, 1, ?, ?)
            """,
            (
                event_id,
                workspace_id,
                kind,
                severity,
                summary,
                json.dumps(details, sort_keys=True, default=str),
                target_type,
                target_id,
                now_iso,
                now_iso,
                now_iso,
            ),
        )
        return RecurrenceUpsertResult(
            event_id=event_id, is_new=True, recurrence_count=1, crossed_threshold=False
        )
    event_id, prev_count = existing
    new_count = prev_count + 1
    conn.execute(
        """
        UPDATE maintenance_events
        SET recurrence_count = ?, last_seen_at = ?, summary = ?, details_json = ?
        WHERE id = ?
        """,
        (
            new_count,
            now_iso,
            summary,
            json.dumps(details, sort_keys=True, default=str),
            event_id,
        ),
    )
    crossed = prev_count < threshold <= new_count
    if crossed:
        insert_audit(
            conn,
            workspace_id=workspace_id,
            action="maintenance.recurrence_threshold_crossed",
            target_type="maintenance_event",
            target_id=event_id,
            after={
                "kind": kind,
                "recurrence_count": new_count,
                "threshold": threshold,
                "at": now_iso,
            },
        )
    return RecurrenceUpsertResult(
        event_id=event_id,
        is_new=False,
        recurrence_count=new_count,
        crossed_threshold=crossed,
    )
