"""Overdue detection and status translation for the sentinel scheduler.

Extracted from ``sentinel_scheduler.py`` to keep that module under the
SLOC budget. Holds the ``passed``/``failed`` -> ``pass``/``fail`` token
translation and the ``workspace_meta``-backed overdue check. Neither
function imports ``sentinel_scheduler`` at module load, so there is no
import cycle.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from agent_memory_lite.config import workspace_meta
from agent_memory_lite.utils.time import parse_iso

_LAST_RUN_KEY = "last_sentinel_run_at"


def _to_sentinel_status(rq_status: str) -> str:
    """Translate a retrieval-quality result status into the persistence/trend
    vocabulary. The runner and ``RetrievalQualityReport`` speak
    ``passed``/``failed`` (retrieval_quality.py), but ``record_sentinel_run``'s
    audit counts, the ``retrieval_sentinel_results`` rows, and the
    ``sentinel_trends`` query all match ``pass``/``fail``. Without this
    translation every recorded status was the wrong token, so the audit log
    and ``/memory/sentinel_trends`` silently reported 0 passes / 0 failures
    regardless of the real results."""
    return {"passed": "pass", "failed": "fail"}.get(rq_status, rq_status)


def _is_overdue(*, db_path: Path, workspace_id: str, threshold_hours: float) -> bool:
    """Read last_sentinel_run_at from workspace_meta. None or stale → overdue."""
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
    except sqlite3.OperationalError:
        return False
    try:
        last_iso = workspace_meta.get(conn, workspace_id, _LAST_RUN_KEY)
    finally:
        conn.close()
    if not last_iso:
        return True
    try:
        last = parse_iso(last_iso)
    except ValueError:
        return True
    elapsed_hours = (datetime.now(UTC) - last).total_seconds() / 3600.0
    return elapsed_hours >= threshold_hours
