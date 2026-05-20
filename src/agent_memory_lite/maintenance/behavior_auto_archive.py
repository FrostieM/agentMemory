"""v3.2 — auto-archive dead behavior_instructions.

Live-2026-05-20 audit on copyBot showed 27 of 91 active behaviors
(application_count=0) had never fired. Each row costs context budget
in every rendered envelope (``<behavior_instructions>``) and trains
the agent to ignore the section because most of it is irrelevant.

This module runs from brain_pass between the consolidation step and
the causal-link step. The criteria are deliberately strict so we
never archive a rule that's actively in use:

* ``active = 1`` (only currently-active rows are candidates)
* ``application_count = 0`` (never fired in the lifetime of the row)
* ``last_applied_at IS NULL`` (no apply trace recorded either)
* ``pinned = 0`` (operator-pinned discipline rules are untouchable)
* ``priority != 'system'`` (system-priority rows are infrastructure)
* ``age(created_at) > N days`` (give a rule time to be observed at least once)

Archive is soft: we set ``active=0`` + stamp
``reviewed_by='auto_archive_v3_2'`` + ``reviewed_at=now`` +
``expires_at=now`` so the row is recoverable via SQL and shows up as
"archived by auto" in the audit trail. Operator can unarchive with
``memory_archive`` (the reverse path) or by setting ``active=1``.

Failure-soft: a missing column in a pre-migration DB returns a
zero-count report instead of raising — brain_pass keeps going.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class ArchiveReport:
    """Per-pass summary — surfaces in BrainPassReport telemetry."""

    archived: int
    scanned: int


_REVIEWED_BY_TAG = "auto_archive_v3_2"


def _cutoff_iso(age_days: int) -> str:
    """Return ISO timestamp ``age_days`` ago in UTC (TZ-aware)."""
    return (datetime.now(UTC) - timedelta(days=age_days)).isoformat()


def auto_archive_dead_behaviors(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    age_days: int = 30,
) -> ArchiveReport:
    """Archive never-fired behaviors older than ``age_days``.

    Returns ``ArchiveReport(archived=N, scanned=M)`` where ``M`` is
    the count of rows that matched the candidate filter and ``N`` is
    the count actually flipped (equal in normal operation).
    """
    cutoff = _cutoff_iso(age_days)
    now = iso_now()
    try:
        cur = conn.execute(
            """SELECT COUNT(*) FROM behavior_instructions
               WHERE workspace_id = ?
                 AND active = 1
                 AND COALESCE(application_count, 0) = 0
                 AND last_applied_at IS NULL
                 AND COALESCE(pinned, 0) = 0
                 AND priority != 'system'
                 AND created_at < ?""",
            (workspace_id, cutoff),
        )
    except sqlite3.OperationalError:
        # Pre-migration DB lacking one of the columns — fail soft.
        return ArchiveReport(archived=0, scanned=0)
    row = cur.fetchone()
    scanned = int(row[0]) if row else 0
    if scanned == 0:
        return ArchiveReport(archived=0, scanned=0)
    try:
        conn.execute(
            """UPDATE behavior_instructions
               SET active = 0,
                   expires_at = COALESCE(expires_at, ?),
                   reviewed_by = ?,
                   reviewed_at = ?,
                   updated_at = ?
               WHERE workspace_id = ?
                 AND active = 1
                 AND COALESCE(application_count, 0) = 0
                 AND last_applied_at IS NULL
                 AND COALESCE(pinned, 0) = 0
                 AND priority != 'system'
                 AND created_at < ?""",
            (now, _REVIEWED_BY_TAG, now, now, workspace_id, cutoff),
        )
        conn.commit()
    except sqlite3.OperationalError:
        return ArchiveReport(archived=0, scanned=scanned)
    return ArchiveReport(archived=scanned, scanned=scanned)
