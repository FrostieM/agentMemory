"""Build the ``active_memory`` snapshot for /memory/status.

Lifted out of ``memory_status.py`` to keep that module under the
150-SLOC ceiling. Calls the same scanners that the dedicated routes
+ MCP tools use, so the dashboard count never drifts from what the
operator sees on a per-route call.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.schemas.memory_status import ActiveMemoryCounts


def _safe_scan_count(call) -> tuple[int, bool]:  # type: ignore[no-untyped-def]
    """Run a scanner that may raise ``OperationalError`` on schema-missing
    DBs. Returns (count, available_flag) so the dashboard can show the
    'feature requires schema upgrade' case as ``available=False``."""
    try:
        rows = call()
    except sqlite3.OperationalError:
        return 0, False
    return len(rows), True


def _persisted_warnings_new(conn: sqlite3.Connection, workspace_id: str) -> int:
    """Count Vector 5 warnings landed as candidate(kind=predictive_warning)
    in 'new' state — i.e. waiting on operator review."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM candidates "
            "WHERE workspace_id = ? AND kind = 'predictive_warning' "
            "AND status = 'new'",
            (workspace_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] if row else 0)


def build_active_memory(conn: sqlite3.Connection, workspace_id: str) -> ActiveMemoryCounts:
    """Compose all v3.1 vector counts into one dashboard snapshot.

    Failure-soft: any single vector's scanner failing only flips that
    vector's ``available=False``; the dashboard stays renderable.
    """
    # Vector 1 — proposals.
    from agent_memory_lite.maintenance import experiment_proposal as ep  # noqa: PLC0415

    proposals_enabled = ep.is_enabled()
    if proposals_enabled:
        n_proposals, proposals_available = _safe_scan_count(
            lambda: ep.find_proposal_candidates(conn, workspace_id=workspace_id)
        )
    else:
        n_proposals, proposals_available = 0, True
    # Vector 3 — blindspots.
    from agent_memory_lite.maintenance import blindspot_detection as bs  # noqa: PLC0415
    from agent_memory_lite.maintenance.blindspot_filters import (  # noqa: PLC0415
        is_enabled as bs_enabled,
    )

    blindspots_enabled = bs_enabled()
    if blindspots_enabled:
        n_blindspots, _ok = _safe_scan_count(
            lambda: bs.find_blindspots(conn, workspace_id=workspace_id)
        )
    else:
        n_blindspots = 0
    # Vector 5 — predictive warnings.
    from agent_memory_lite.maintenance import predictive_failure as pf  # noqa: PLC0415

    pf_enabled = pf.is_enabled()
    if pf_enabled:
        n_warnings, warnings_available = _safe_scan_count(
            lambda: pf.find_predictive_warnings(conn, workspace_id=workspace_id)
        )
    else:
        n_warnings, warnings_available = 0, True
    return ActiveMemoryCounts(
        open_proposals=n_proposals,
        proposals_available=proposals_available,
        proposals_enabled=proposals_enabled,
        blindspots=n_blindspots,
        blindspots_enabled=blindspots_enabled,
        predictive_warnings=n_warnings,
        predictive_warnings_available=warnings_available,
        predictive_warnings_enabled=pf_enabled,
        persisted_warnings_new=_persisted_warnings_new(conn, workspace_id),
    )
