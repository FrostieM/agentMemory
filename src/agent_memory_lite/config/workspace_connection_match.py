"""Low-level physical-DB-path helpers for the workspace connection guard.

Split out of ``workspace_paths`` so each module stays at or below the 150-SLOC
ceiling (one concern per module). These two helpers are dependency-free (they
do not touch the registry), so the higher-level resolver in ``workspace_paths``
imports them without a cycle. Re-exported from ``workspace_paths`` for
back-compat with existing importers (api/workspace_routing + its tests).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def _connection_db_path(conn: sqlite3.Connection) -> str:
    """Absolute file backing the connection's ``main`` schema.

    ``PRAGMA database_list`` reports the physical file SQLite opened.
    Empty for an in-memory or temp database -- the guard then has no
    path to compare against and skips.
    """
    try:
        for row in conn.execute("PRAGMA database_list"):
            if row[1] == "main":
                return str(row[2] or "")
    except sqlite3.Error:
        return ""
    return ""


def _connections_match(actual: str, expected: str) -> bool:
    """True when ``actual`` and ``expected`` name the same physical file
    -- or cannot be told apart.

    ``os.path.samefile`` compares device + inode, so it sees through
    symlinks, ``subst`` drives and UNC-vs-drive-letter forms that a
    ``Path.resolve()`` string compare can miss. It needs both files to
    exist; when one does not, fall back to a resolved-path compare. When
    even that fails, treat the pair as matching -- a transient
    resolution glitch must never false-reject a legitimate write.
    """
    try:
        return os.path.samefile(actual, expected)
    except OSError:
        pass
    try:
        return Path(actual).resolve() == Path(expected).resolve()
    except (OSError, ValueError):
        return True
