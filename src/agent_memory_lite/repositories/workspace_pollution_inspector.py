"""Cross-table workspace_id row counts for pollution detection.

Split out of ``workspace_manifest_repo.py`` so the manifest guard stays
under the SLOC ceiling. Used both by the manifest's first-run sanity
check (refuse to claim a DB whose existing rows belong to a different
workspace) and by ``scripts/memory_workspace_doctor.py``.
"""

from __future__ import annotations

import sqlite3


def _workspace_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type IN ('table', 'virtual table') AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    tables: list[str] = []
    for row in rows:
        table = str(row[0])
        try:
            cols = [str(col[1]) for col in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        except sqlite3.OperationalError:
            continue
        if "workspace_id" in cols:
            tables.append(table)
    return tables


def infer_workspace_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Sum rows per workspace_id across every workspace-tagged table."""
    counts: dict[str, int] = {}
    for table in _workspace_tables(conn):
        rows = conn.execute(
            f"""
            SELECT workspace_id, COUNT(*) AS n
            FROM {table}
            GROUP BY workspace_id
            """
        ).fetchall()
        for row in rows:
            workspace_id = str(row["workspace_id"])
            counts[workspace_id] = counts.get(workspace_id, 0) + int(row["n"])
    return counts
