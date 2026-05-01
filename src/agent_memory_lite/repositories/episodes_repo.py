"""SQL operations for the `episodes` table."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import Episode, EpisodeIn
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def _row_label(row: sqlite3.Row) -> str | None:
    """Read `label` column if migration 0015 has applied; tolerate older DBs."""
    try:
        value = row["label"]
    except (IndexError, KeyError):
        return None
    return value if value else None


def _row_to_episode(row: sqlite3.Row) -> Episode:
    return Episode(
        id=row["id"],
        workspace_id=row["workspace_id"],
        session_id=row["session_id"],
        task_id=row["task_id"],
        source_type=EpisodeSource(row["source_type"]),
        raw_text=row["raw_text"],
        summary=row["summary"],
        label=_row_label(row),
        trust_level=TrustLevel(row["trust_level"]),
        importance=float(row["importance"]),
        confidence=float(row["confidence"]),
        created_at=row["created_at"],
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def insert_episode(
    conn: sqlite3.Connection,
    episode_in: EpisodeIn,
    *,
    redacted_text: str | None = None,
) -> Episode:
    episode_id = new_id(IdKind.EPISODE)
    created_at = iso_now()
    raw_text = redacted_text if redacted_text is not None else episode_in.raw_text
    conn.execute(
        """
        INSERT INTO episodes (
            id, workspace_id, session_id, task_id, source_type, raw_text, summary,
            label, trust_level, importance, confidence, created_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            episode_id,
            episode_in.workspace_id,
            episode_in.session_id,
            episode_in.task_id,
            episode_in.source_type.value,
            raw_text,
            episode_in.summary,
            episode_in.label,
            episode_in.trust_level.value,
            episode_in.importance,
            episode_in.confidence,
            created_at,
            json.dumps(episode_in.metadata, sort_keys=True),
        ),
    )
    return Episode(
        id=episode_id,
        workspace_id=episode_in.workspace_id,
        session_id=episode_in.session_id,
        task_id=episode_in.task_id,
        source_type=episode_in.source_type,
        raw_text=raw_text,
        summary=episode_in.summary,
        label=episode_in.label,
        trust_level=episode_in.trust_level,
        importance=episode_in.importance,
        confidence=episode_in.confidence,
        created_at=created_at,
        metadata=episode_in.metadata,
    )


def get_episode(conn: sqlite3.Connection, episode_id: str) -> Episode | None:
    row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
    return _row_to_episode(row) if row is not None else None


def list_recent_episodes(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    limit: int = 8,
) -> list[Episode]:
    rows = conn.execute(
        """
        SELECT * FROM episodes
        WHERE workspace_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (workspace_id, limit),
    ).fetchall()
    return [_row_to_episode(r) for r in rows]
