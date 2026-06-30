"""Brain-pass maintenance hook for the sentinel scheduler.

Extracted from ``sentinel_scheduler.py`` to keep that module under the
SLOC budget. Holds the v3 brain-maintenance sub-routine (Path B of the
sentinel pass) and the standalone ``_stamp_last_run`` helper. These have
no dependency back on ``sentinel_scheduler`` at import time, so there is
no import cycle.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from agent_memory_lite.config import workspace_meta
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.pragmas import enable_foreign_keys
from agent_memory_lite.utils.time import iso_now

_LAST_RUN_KEY = "last_sentinel_run_at"
_log = logging.getLogger(__name__)


def _maybe_run_brain_pass(
    conn: sqlite3.Connection, *, workspace_id: str, settings: Settings
) -> None:
    """Best-effort v3 brain maintenance. Never raises -- single bad
    workspace cannot block the sentinel commit."""
    try:
        from agent_memory_lite.maintenance.brain_pass import run_brain_pass  # noqa: PLC0415

        if not getattr(settings, "brain_pass_enabled", True):
            return
        run_brain_pass(conn, workspace_id=workspace_id, settings=settings)
        # FK-drift sentinel: the connection is now FK-enforced, but record any
        # pre-existing dangling reference (e.g. from a legacy pre-FK-on write or
        # a foreign path) as a maintenance event so the operator sees drift in
        # /health instead of months later. Boot-time only previously (app.py);
        # this extends it to every maintenance tick. Cheap + idempotent.
        from agent_memory_lite.db.integrity_check import (  # noqa: PLC0415
            record_foreign_key_violations,
        )

        record_foreign_key_violations(conn, workspace_id=workspace_id)
    except Exception:  # pragma: no cover - defensive
        _log.exception("brain_pass failed for workspace=%s", workspace_id)


def _stamp_last_run(*, db_path: Path, workspace_id: str) -> None:
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        # M9/round-A: enable_foreign_keys moved INSIDE the try -- if it raised, the
        # open connection leaked because the finally was never entered.
        enable_foreign_keys(conn)
        workspace_meta.set_value(conn, workspace_id, _LAST_RUN_KEY, iso_now())
        conn.commit()
    finally:
        conn.close()
