"""Application-managed FTS5 sync for the ``durable_fts`` virtual table.

The durable knowledge kinds (decision / theory / behavior / skill / concept /
insight) are indexed for BM25 relevance retrieval. Like ``chunks_fts`` this is
NOT trigger-driven: every durable create/edit must route through ``sync_durable_fts``
(wired into ``ingestion.canonical_writer.write_canonical`` and
``storage.writer.edit``). The table itself is created + back-filled by
``migrations/0007_durable_fts.sql``.

``content`` for a row is the kind's free-text columns concatenated. The column
lists MUST stay in sync with ``storage.reader._KIND_FTS_COLUMNS`` (the LIKE path
for the other kinds) and the backfill in ``migrations/0007_durable_fts.sql``.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.logging_setup import get_logger

_log = get_logger("fts.durable_fts")

# kind -> source table (the 6 durable knowledge kinds only).
_KIND_TABLE: dict[str, str] = {
    "decision": "decisions",
    "theory": "theories",
    "behavior": "behaviors",
    "skill": "skills",
    "concept": "concepts",
    "insight": "insights",
}

# kind -> free-text columns concatenated into the FTS ``content`` (mirror of the
# durable rows of storage.reader._KIND_FTS_COLUMNS + migrations/0007).
_FTS_COLUMNS: dict[str, list[str]] = {
    "decision": ["title", "decision_text", "rationale", "gist"],
    "theory": ["title", "claim", "mechanism", "gist"],
    "behavior": ["name", "rule", "rationale", "rule_one_line"],
    "skill": ["name", "summary", "when_to_use_short"],
    "concept": ["name", "definition", "definition_one_line"],
    "insight": ["summary", "gist", "proposed_action"],
}

# The kinds routed to the durable FTS5/BM25 path (vs the LIKE interim path).
DURABLE_FTS_KINDS = frozenset(_KIND_TABLE)


def _content_from_values(values: list[object]) -> str:
    """Join the non-NULL free-text column values into one FTS content blob."""
    return " ".join(str(v) for v in values if v is not None)


def _table_has_durable_fts(conn: sqlite3.Connection) -> bool:
    """True when the durable_fts virtual table exists (pre-migration safety)."""
    try:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='durable_fts'"
            ).fetchone()
            is not None
        )
    except sqlite3.Error:
        return False


def sync_durable_fts(
    conn: sqlite3.Connection, *, kind: str, object_id: str, workspace_id: str
) -> bool:
    """Re-index one durable row's FTS content (delete old + insert fresh).

    No-op for non-durable kinds or a pre-0007 DB. Reads the kind's free-text
    columns positionally so it does not depend on the connection's row_factory.

    Atomic + observable (reliability audit 2026-06-25). Connections are
    autocommit, so the old code auto-committed the DELETE and the INSERT
    *separately*: a failed INSERT left the row permanently de-indexed
    (silently unsearchable). Now the DELETE+INSERT run inside one ``with_tx``
    unit, so a failure rolls the DELETE back -- the row's prior FTS entry
    survives. Still failure-soft for the caller (never raises into the durable
    write), but no longer silent: the failure is logged and returned so the
    create choke point can record extra context. The brain-pass rebuild
    backstop then re-indexes the row on its next tick -- but only when
    ``brain_pass_enabled`` is on (the default); if the operator disabled the
    background path, a failed sync stays unsearchable until a manual
    ``rebuild_durable_fts``. Returns True on success/no-op, False when the sync
    was rolled back.
    """
    if kind not in DURABLE_FTS_KINDS or not _table_has_durable_fts(conn):
        return True
    table = _KIND_TABLE[kind]
    cols = _FTS_COLUMNS[kind]
    try:
        with with_tx(conn):
            row = conn.execute(
                f"SELECT {', '.join(cols)} FROM {table} WHERE id = ? AND workspace_id = ?",
                (object_id, workspace_id),
            ).fetchone()
            # round-C: scope the DELETE to workspace_id too. object_id is a globally
            # unique PK so today this only ever hits the right row, but adding the
            # filter makes the FTS delete defensively consistent with the SELECT above
            # -- a mis-passed workspace_id can never evict another workspace's index row.
            conn.execute(
                "DELETE FROM durable_fts WHERE object_id = ? AND workspace_id = ?",
                (object_id, workspace_id),
            )
            if row is not None:
                conn.execute(
                    "INSERT INTO durable_fts (object_id, workspace_id, kind, content) "
                    "VALUES (?, ?, ?, ?)",
                    (object_id, workspace_id, kind, _content_from_values(list(row))),
                )
        return True
    except sqlite3.Error as exc:
        _log.warning(
            "durable_fts_sync_failed",
            kind=kind,
            object_id=object_id,
            workspace_id=workspace_id,
            error=str(exc),
        )
        return False


def delete_durable_fts(conn: sqlite3.Connection, object_id: str) -> int:
    """Drop a row's FTS entry (e.g. on hard delete). Returns rows removed."""
    if not _table_has_durable_fts(conn):
        return 0
    cur = conn.execute("DELETE FROM durable_fts WHERE object_id = ?", (object_id,))
    return int(cur.rowcount)


def rebuild_durable_fts(conn: sqlite3.Connection, workspace_id: str | None = None) -> int:
    """Drop + re-populate durable_fts for ``workspace_id`` (or every workspace).

    Repair/backfill helper -- the migration does the initial backfill; this
    re-syncs after a bulk edit or a corrupted index. Returns rows written.
    """
    if not _table_has_durable_fts(conn):
        return 0
    # M2 (reliability audit): the wipe + full re-populate are ONE atomic unit.
    # Connections are autocommit, so the old code committed the DELETE first --
    # a crash (or a single bad row) partway through the INSERT loop left the
    # index half-empty, silently hiding every not-yet-reinserted durable row
    # from memory_search. with_tx rolls the DELETE back on any failure, so the
    # prior (complete) index survives intact. SAVEPOINT-safe under an outer tx.
    written = 0
    with with_tx(conn):
        if workspace_id is None:
            conn.execute("DELETE FROM durable_fts")
        else:
            conn.execute("DELETE FROM durable_fts WHERE workspace_id = ?", (workspace_id,))
        for kind, table in _KIND_TABLE.items():
            cols = _FTS_COLUMNS[kind]
            ws_clause = "" if workspace_id is None else " AND workspace_id = ?"
            params = () if workspace_id is None else (workspace_id,)
            rows = conn.execute(
                f"SELECT id, workspace_id, {', '.join(cols)} FROM {table} WHERE 1 = 1{ws_clause}",
                params,
            ).fetchall()
            for row in rows:
                vals = list(row)
                conn.execute(
                    "INSERT INTO durable_fts (object_id, workspace_id, kind, content) "
                    "VALUES (?, ?, ?, ?)",
                    (vals[0], vals[1], kind, _content_from_values(vals[2:])),
                )
                written += 1
    return written
