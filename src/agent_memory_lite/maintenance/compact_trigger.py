"""Token-aware compaction watchdog.

Inspects the current chunk count + age distribution and emits a
``compaction_due`` maintenance event when the workspace is past
the operator-configured threshold. Does NOT run compaction
itself — surfacing the signal is enough; the operator runs
``/memory/compact`` (or schedules it) when they're ready.

Off by default. Enable with
``MEMORY_COMPACT_TRIGGER_THRESHOLD_CHUNKS`` > 0 in the project
settings.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.ingestion.maintenance_writer import write_maintenance_event
from agent_memory_lite.models.enums import MaintenanceEventStatus, MaintenanceSeverity
from agent_memory_lite.models.maintenance import MaintenanceEventIn
from agent_memory_lite.repositories.maintenance_repo import list_maintenance_events


def _chunk_stats(conn: sqlite3.Connection, *, workspace_id: str) -> tuple[int, int]:
    """Return ``(total_chunks, stale_chunks)`` for ``workspace_id``."""
    total_row = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()
    stale_row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM chunks
        WHERE workspace_id = ?
        AND created_at < datetime('now', '-90 days')
        """,
        (workspace_id,),
    ).fetchone()
    total = int(total_row["n"]) if total_row else 0
    stale = int(stale_row["n"]) if stale_row else 0
    return total, stale


def _open_compact_event_exists(conn: sqlite3.Connection, *, workspace_id: str) -> bool:
    """Return True if there's already an open compaction_due event so
    we don't write a duplicate on every poll."""
    events = list_maintenance_events(
        conn,
        workspace_id=workspace_id,
        statuses=[MaintenanceEventStatus.OPEN],
        limit=200,
    )
    return any(event.kind == "compaction_due" for event in events)


def check_compaction_threshold(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Probe the chunk count + stale ratio and emit a maintenance
    event when the threshold is crossed. Returns the probe result
    so the caller can render a UI hint or log.
    """
    cfg = settings if settings is not None else get_settings()
    threshold = int(getattr(cfg, "compact_trigger_threshold_chunks", 0))
    if threshold <= 0:
        return {"enabled": False, "total": 0, "stale": 0, "threshold": 0, "event_written": False}
    total, stale = _chunk_stats(conn, workspace_id=workspace_id)
    triggered = total >= threshold and stale > 0
    event_written = False
    if triggered and not _open_compact_event_exists(conn, workspace_id=workspace_id):
        write_maintenance_event(
            conn,
            MaintenanceEventIn(
                workspace_id=workspace_id,
                kind="compaction_due",
                severity=MaintenanceSeverity.WARNING,
                summary=(
                    f"Workspace has {total} chunks ({stale} older than 90 days); "
                    f"threshold is {threshold}. Run /memory/compact to summarize "
                    "old chunks and free retrieval budget."
                ),
                details={"total_chunks": total, "stale_chunks": stale, "threshold": threshold},
                target_type="workspace",
                target_id=workspace_id,
            ),
        )
        event_written = True
    return {
        "enabled": True,
        "total": total,
        "stale": stale,
        "threshold": threshold,
        "triggered": triggered,
        "event_written": event_written,
    }
