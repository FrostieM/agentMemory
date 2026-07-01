"""Live-catalog discovery of columns that reference ``episodes.id``.

The episode prune guard in :mod:`row_retention` must know every other table/column
that can reference an episode, of BOTH shapes -- scalar FK columns and JSON-array
columns -- so a prune never orphans a citing row. These helpers discover those
references from the live SQLite catalog rather than hardcoding them, so a newly
added referencing column of either shape is covered automatically as the schema
evolves.
"""

from __future__ import annotations

import sqlite3

# Columns by which other tables reference episodes.id (used for catalog discovery).
_EPISODE_REF_COLUMNS = ("source_episode_id", "evidence_episode_id", "episode_id")


def _episode_referencing_pairs(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(table, column) pairs that reference ``episodes.id``, from the live catalog.

    Discovered rather than hardcoded so the episode prune guard can never miss a
    referencing table as the schema evolves.
    """
    pairs: list[tuple[str, str]] = []
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'episodes'"
    ).fetchall()
    for (table,) in rows:
        try:
            # round-D: bind the table name via the pragma_table_info() table-valued
            # function instead of interpolating it into `PRAGMA table_info('{table}')`.
            # A table whose name contains a single quote produced invalid SQL that was
            # silently swallowed, dropping that table from the episode-FK guard.
            cols = {
                row[0] for row in conn.execute("SELECT name FROM pragma_table_info(?)", (table,))
            }
        except sqlite3.Error:
            continue
        pairs.extend((table, col) for col in _EPISODE_REF_COLUMNS if col in cols)
    return pairs


def _episode_json_referencing_pairs(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(table, column) pairs that reference episodes via a JSON ARRAY of ids --
    columns whose name ends with ``episode_ids_json`` (today only
    ``insights.source_episode_ids_json``, which consolidation fills with
    file_indexed episode ids).

    These hold a JSON list, not a scalar FK. ``_episode_referencing_pairs``
    matches only the exact scalar names in ``_EPISODE_REF_COLUMNS`` and so does
    NOT find them, and a scalar ``= episodes.id`` join cannot read array elements
    -- hence this separate suffix-based discovery, feeding a ``json_each`` guard.
    Discovered by suffix so a future such column is covered automatically.
    """
    pairs: list[tuple[str, str]] = []
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'episodes'"
    ).fetchall()
    for (table,) in rows:
        try:
            # round-D: bind the table name (see _episode_referencing_pairs) instead of
            # interpolating it into a PRAGMA -- injection-safe + quote-name-safe.
            cols = [
                row[0] for row in conn.execute("SELECT name FROM pragma_table_info(?)", (table,))
            ]
        except sqlite3.Error:
            continue
        pairs.extend((table, col) for col in cols if col.endswith("episode_ids_json"))
    return pairs
