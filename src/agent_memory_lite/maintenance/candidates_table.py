"""Resolve the live candidates table name (memory_candidates OR candidates).

Final-audit H3 fix: both ``experiment_proposal_writer`` and
``predictive_failure_writer`` previously hardcoded the legacy name
``memory_candidates``. On canonical-only deploys (where the canonical
v3 schema replaces the legacy table with ``candidates``), the writers
would silently raise ``OperationalError`` while the dashboard reader's
fallback path made the failure invisible.

Both tables ship with the same column layout (per
``migrations/canonical/0001_init.sql:676`` and
``migrations/0001_init.sql:564``) so we can pick whichever exists at
runtime and emit the same INSERT.

The resolver is per-call (no caching) so a mid-process schema upgrade
still works. Cost: one ``sqlite_master`` SELECT per write — negligible
compared to the INSERT.
"""

from __future__ import annotations

import sqlite3


def resolve_candidates_table(conn: sqlite3.Connection) -> str | None:
    """Return the live table name (``memory_candidates`` if present,
    else ``candidates``, else ``None`` when neither exists).

    Callers that get ``None`` should treat the write as a no-op and
    surface the missing-schema state to the operator.
    """
    for name in ("memory_candidates", "candidates"):
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            if row:
                return name
        except sqlite3.Error:
            continue
    return None
