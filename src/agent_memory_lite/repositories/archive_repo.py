"""Universal archive flag for chunks / episodes / files.

The other "kinds" (decisions, theories, insights, behavior_instructions,
roles, skills, playbooks, candidates) already have their own soft-delete
fields - status='archived'/'superseded'/'rejected' or active=0. The
retrievable text rows did not, so this module adds a tiny
``set_<kind>_archived(...)`` per text-row table and a single dispatch
helper the service layer calls so handlers don't fan out per kind.
"""

from __future__ import annotations

import sqlite3

_TABLE_BY_KIND = {
    "chunk": "chunks",
    "episode": "episodes",
    "file": "files",
}


def set_text_row_archived(
    conn: sqlite3.Connection,
    *,
    kind: str,
    workspace_id: str,
    object_id: str,
    archived: bool,
) -> bool:
    """Flip the ``is_archived`` flag for a chunk / episode / file row.

    Returns True when a row matched (and was updated). Refuses to write
    rows that don't belong to ``workspace_id`` so a project chat cannot
    archive a row that lives in another workspace by guessing its id.
    """
    table = _TABLE_BY_KIND.get(kind)
    if table is None:
        raise ValueError(f"unsupported archive kind: {kind!r}")
    cur = conn.execute(
        f"UPDATE {table} SET is_archived = ? WHERE id = ? AND workspace_id = ?",
        (1 if archived else 0, object_id, workspace_id),
    )
    conn.commit()
    return cur.rowcount > 0


def is_text_row_archived(
    conn: sqlite3.Connection,
    *,
    kind: str,
    object_id: str,
) -> bool:
    table = _TABLE_BY_KIND.get(kind)
    if table is None:
        return False
    row = conn.execute(
        f"SELECT is_archived FROM {table} WHERE id = ?",
        (object_id,),
    ).fetchone()
    if row is None:
        return False
    return bool(row[0])


def chunk_archive_lookup(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    chunk_ids: list[str],
) -> dict[str, bool]:
    """Bulk lookup of ``is_archived`` for a set of chunk ids.

    Used by the search response shaper so each hit can be tagged with
    the per-row archive flag without firing one query per id. Empty
    input returns ``{}`` cheaply.
    """
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    rows = conn.execute(
        f"SELECT id, is_archived FROM chunks WHERE workspace_id = ? AND id IN ({placeholders})",
        (workspace_id, *chunk_ids),
    ).fetchall()
    return {row[0]: bool(row[1]) for row in rows}
