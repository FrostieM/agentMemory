"""Extraction-pending queue helpers for the async write path (Batch E).

``ingest_episode`` marks an episode ``extraction_pending = 1`` when
``MEMORY_DEFER_EXTRACTION`` is on (instead of running the slow extractor inline);
the brain-pass extraction-drain step selects the pending batch, runs the
extractor OFF the request thread, then clears the flag. The partial index
``idx_episodes_extraction_pending`` (migration 0008) keeps both the EXISTS probe
and the ordered batch select O(matches), not O(workspace).

The ``is_archived = 0`` clause is INTENTIONAL, not a bug: an episode archived
(discarded) before the drain runs forgoes its deferred extraction -- archiving an
episode means hiding/discarding it, so extracting fresh ``new`` candidates from a
row the operator just discarded would be wrong. The pending flag on an archived
row is harmless (the partial index excludes it, so it costs nothing and is never
probed). The only behavioural difference from the synchronous path -- which would
have extracted inline before any archive -- is that an episode archived within the
brain-pass window keeps its un-promoted candidates unextracted; that is the
correct discard semantics, not data loss.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.episodes import Episode
from agent_memory_lite.repositories.episodes_repo import _row_to_episode


def mark_episode_extraction_pending(conn: sqlite3.Connection, episode_id: str) -> None:
    """Flag an episode as awaiting deferred extraction (set on async persist)."""
    conn.execute("UPDATE episodes SET extraction_pending = 1 WHERE id = ?", (episode_id,))


def clear_episode_extraction_pending(conn: sqlite3.Connection, episode_id: str) -> None:
    """Clear the pending flag once the deferred extraction has completed."""
    conn.execute("UPDATE episodes SET extraction_pending = 0 WHERE id = ?", (episode_id,))


def has_pending_extraction(conn: sqlite3.Connection, workspace_id: str) -> bool:
    """Cheap O(1) EXISTS probe. ``ORDER BY created_at`` steers SQLite onto the
    partial index so an empty probe (the steady state) costs one index seek."""
    row = conn.execute(
        "SELECT 1 FROM episodes WHERE workspace_id = ? AND extraction_pending = 1 "
        "AND is_archived = 0 ORDER BY created_at LIMIT 1",
        (workspace_id,),
    ).fetchone()
    return row is not None


def list_episodes_pending_extraction(
    conn: sqlite3.Connection, workspace_id: str, *, limit: int
) -> list[Episode]:
    """Oldest-first batch of episodes awaiting deferred extraction, bounded by
    ``limit`` (the per-pass cap), via the partial index."""
    rows = conn.execute(
        "SELECT * FROM episodes WHERE workspace_id = ? AND extraction_pending = 1 "
        "AND is_archived = 0 ORDER BY created_at LIMIT ?",
        (workspace_id, limit),
    ).fetchall()
    return [_row_to_episode(r) for r in rows]
