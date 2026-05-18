"""Phase 6: bi-temporal SQL helpers.

A bi-temporal row carries TWO time axes:
  * ``valid_from`` / ``valid_to``  -- WHEN THE FACT WAS / IS TRUE
  * ``created_at`` / ``updated_at`` -- WHEN WE LEARNED IT

Phase 1 outcome filtering and Phase 5 self-model both depend on selecting
rows whose VALIDITY brackets ``as_of`` (default = now). This module
centralises the SQL fragment so the four kinds that now carry bi-temporal
columns (theories, concepts, behaviors, insights -- decisions already had
them) speak the same dialect.

NULL semantics:
  * ``valid_from IS NULL`` is treated as "valid forever in the past"
    (the fact existed before we wrote it down).
  * ``valid_to IS NULL`` is treated as "valid forever in the future"
    (the fact is still current).

The helper returns a SQL fragment that can be AND-merged into any
existing WHERE clause. It uses positional placeholders so the caller
must pass the ``as_of`` value once per fragment.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.utils.time import iso_now


def where_valid(*, as_of: str | None = None) -> tuple[str, tuple[str, str]]:
    """Return ``(sql_fragment, (as_of, as_of))`` for AND-merging.

    The clause wraps both sides in SQLite's ``datetime()`` function so
    that ISO 8601 strings written by different code paths (Z-suffix
    from ``time.strftime`` vs ``+00:00`` from ``datetime.isoformat``)
    compare correctly. SQLite's ``datetime()`` parses both forms into
    a canonical ``YYYY-MM-DD HH:MM:SS`` representation before the
    inequality check.

    Example:
        clause, params = where_valid(as_of="2026-01-01T00:00:00+00:00")
        conn.execute(f"SELECT * FROM theories WHERE {clause}", params)
    """
    iso = as_of or iso_now()
    return (
        "(valid_from IS NULL OR datetime(valid_from) <= datetime(?)) "
        "AND (valid_to IS NULL OR datetime(valid_to) > datetime(?))",
        (iso, iso),
    )


def has_validity_columns(conn: sqlite3.Connection, table: str) -> bool:
    """True if the table has ``valid_from`` and ``valid_to`` columns.

    Used by the reader to skip the bi-temporal filter on pre-migration
    DBs (the columns do not exist, so the WHERE clause would fail).
    Cheap: one PRAGMA call per table per connection lifetime; callers
    typically memoise.
    """
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return False
    names = {row[1] for row in rows}  # row[1] is the column name
    return {"valid_from", "valid_to"}.issubset(names)
