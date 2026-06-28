"""Substrate-degradation read helpers over ``maintenance_events``.

Split out of ``maintenance_queries.py`` (SLOC ceiling, one concern per module).
These power the fast-path degradation surfaces -- GET /memory/status, the MCP
``memory_status`` handler, shallow ``/health``, and the brief banner -- so a
degraded substrate (open ERROR/CRITICAL maintenance_events such as
``vector_upsert_failed`` / ``durable_fts_sync_failed`` /
``brain_pass_step_failed``) is visible in one read instead of only via the
30-60s deep ``/health?deep=true`` audit (reliability audit M6/L4).
"""

from __future__ import annotations

import sqlite3

# Severities that mean the substrate is actually DEGRADED (flip the degraded
# flag / brief banner / health status); WARNING/INFO are housekeeping and do not.
_DEGRADED_SEVERITIES = frozenset({"error", "critical"})


def open_maintenance_degradations(
    conn: sqlite3.Connection, *, workspace_id: str
) -> list[tuple[str, str, int]]:
    """Open maintenance events grouped by (kind, severity) -- the one-read
    degradation breakdown. Cheap: a single indexed GROUP BY over the workspace +
    open filter. Returns rows of ``(kind, severity, count)`` ordered by count
    desc."""
    rows = conn.execute(
        """
        SELECT kind, severity, COUNT(*) AS n
        FROM maintenance_events
        WHERE workspace_id = ? AND status = 'open'
        GROUP BY kind, severity
        ORDER BY n DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [(str(r[0]), str(r[1]), int(r[2])) for r in rows]


def degradation_banner(conn: sqlite3.Connection, *, workspace_id: str) -> str | None:
    """A one-line session-start banner when the substrate is DEGRADED (open
    ERROR/CRITICAL maintenance_events), else None.

    Surfaced in the brief (the session-start surface) so an agent that opens
    with ``memory_brief`` -- per the discipline rules, NOT necessarily
    ``memory_status`` -- still SEES degradation instead of a healthy-looking
    brief (M6/L4: the brief was the one agent surface that still silently
    succeeded). Failure-soft on a pre-migration DB."""
    try:
        rows = open_maintenance_degradations(conn, workspace_id=workspace_id)
    except sqlite3.OperationalError:
        return None
    bad = [(kind, n) for kind, sev, n in rows if sev.lower() in _DEGRADED_SEVERITIES]
    if not bad:
        return None
    total = sum(n for _, n in bad)
    kinds = ", ".join(f"{kind} x{n}" for kind, n in bad[:3])
    return (
        f"MEMORY SUBSTRATE DEGRADED: {total} open issue(s) ({kinds}). "
        "Call memory_status for detail; recent reads/writes may be incomplete."
    )
