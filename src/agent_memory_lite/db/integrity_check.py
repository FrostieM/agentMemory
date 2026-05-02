"""Boot-time data-integrity checks.

Right now the only check here is foreign-key drift: SQLite enforces
FK constraints per-connection (``PRAGMA foreign_keys=ON``), so a
write path that bypasses ``open_connection`` can silently produce
dangling references. ``record_foreign_key_violations`` runs
``PRAGMA foreign_key_check`` and writes a single
``retrieval_integrity`` maintenance event when violations exist, so
the operator sees the drift in ``/health`` and ``hygiene_report``
instead of finding it months later.

Designed to be cheap: ``foreign_key_check`` is a constant-factor walk
over every FK in the schema. We only insert a maintenance event when
the count is non-zero AND no equivalent open event already exists, so
repeated boots don't spam the table.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Any

from agent_memory_lite.models.enums import MaintenanceEventStatus, MaintenanceSeverity
from agent_memory_lite.repositories.maintenance_repo import (
    insert_maintenance_event_row,
    list_maintenance_events,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

EVENT_KIND = "foreign_key_drift"


def _existing_open_drift_event(conn: sqlite3.Connection, workspace_id: str) -> bool:
    try:
        events = list_maintenance_events(
            conn,
            workspace_id=workspace_id,
            statuses=[MaintenanceEventStatus.OPEN],
            limit=20,
        )
    except sqlite3.OperationalError:
        return False
    return any(event.kind == EVENT_KIND for event in events)


def record_foreign_key_violations(conn: sqlite3.Connection, *, workspace_id: str) -> dict[str, Any]:
    """Walk PRAGMA foreign_key_check and record a maintenance event.

    Returns a small report dict so callers can surface the result in
    logs / `/health` without re-querying. The event is only inserted
    when a non-zero violation count is seen AND no open
    ``foreign_key_drift`` event already exists for the workspace.
    """
    rows = list(conn.execute("PRAGMA foreign_key_check"))
    if not rows:
        return {"violations": 0, "by_table": {}, "event_id": None}

    by_pair: Counter[tuple[str, str]] = Counter()
    for row in rows:
        # row = (child_table, child_rowid, parent_table, fkid)
        child = str(row[0]) if row[0] is not None else "?"
        parent = str(row[2]) if row[2] is not None else "?"
        by_pair[(child, parent)] += 1

    if _existing_open_drift_event(conn, workspace_id):
        return {
            "violations": len(rows),
            "by_table": {f"{c}->{p}": n for (c, p), n in by_pair.most_common()},
            "event_id": None,
        }

    event_id = new_id(IdKind.MAINTENANCE_EVENT)
    summary = (
        f"{len(rows)} dangling foreign-key reference(s) detected on startup. "
        "Run scripts/repair_dangling_source_refs.py --apply --backup-first "
        "or scripts/memory_audit.py --json for details."
    )
    details = {
        "violations": len(rows),
        "by_table": {f"{c}->{p}": n for (c, p), n in by_pair.most_common()},
    }
    insert_maintenance_event_row(
        conn,
        event_id=event_id,
        workspace_id=workspace_id,
        kind=EVENT_KIND,
        severity=MaintenanceSeverity.WARNING,
        status=MaintenanceEventStatus.OPEN,
        summary=summary,
        details=details,
        source_episode_id=None,
        target_type=None,
        target_id=None,
        created_at=iso_now(),
    )
    conn.commit()
    return {**details, "event_id": event_id}
