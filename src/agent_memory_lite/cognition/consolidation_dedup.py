"""Idempotency guard for sleep-time consolidation (H6, global-audit 2026-06-30).

``consolidate_workspace`` re-clusters a sliding window of recent episodes on every
cron tick (default 24h window). With any sub-24h cadence, consecutive runs
re-cluster the SAME episodes, and ``_persist_insight`` -- which mints a random
uuid per row -- used to INSERT a DUPLICATE candidate every tick, polluting the
review queue. The module's docstring claimed an idempotency it did not have.

``evidence_seen`` makes it real: an insight whose evidence episode set was ALREADY
consolidated (in any status/type) is not persisted again. A CHANGED set (a new
episode joined the cluster) is a genuinely new insight -- exactly the promised
"only emits new candidates if new episodes appeared" semantics.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable


def evidence_seen(
    conn: sqlite3.Connection, *, workspace_id: str, evidence_ids: Iterable[str]
) -> bool:
    """True if an insight with the SAME evidence episode set already exists for the
    workspace, so this cluster must not be re-persisted as a duplicate."""
    target = frozenset(e for e in evidence_ids if e)
    if not target:
        return False
    try:
        rows = conn.execute(
            "SELECT source_episode_ids_json FROM insights WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return False
    for row in rows:
        try:
            existing = frozenset(json.loads(row[0] or "[]"))
        except (json.JSONDecodeError, TypeError):
            continue
        if existing == target:
            return True
    return False
