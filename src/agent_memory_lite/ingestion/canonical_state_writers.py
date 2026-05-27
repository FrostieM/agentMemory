"""State-like business writers used by the compact canonical write surface."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.task_state_writer import write_task_state
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.task_state import TaskStateIn
from agent_memory_lite.storage.reader import get_object


def write_episode_canonical(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    result = ingest_episode(conn, EpisodeIn(**payload))
    out = get_object(
        conn, workspace_id=workspace_id, kind="episode", object_id=result.episode.id
    ) or {
        "id": result.episode.id,
        "kind": "episode",
    }
    return {
        **out,
        "episode_id": result.episode.id,
        "chunk_id": result.chunk.id,
        "redacted_kinds": result.redacted_kinds,
        "embedded": result.embedded,
        "was_duplicate": result.was_duplicate,
    }


def write_task_canonical(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    state = write_task_state(conn, TaskStateIn(**payload))
    out = get_object(conn, workspace_id=workspace_id, kind="task", object_id=state.id) or {
        "id": state.id,
        "kind": "task",
    }
    return {
        **out,
        "state_id": state.id,
        "task_id": state.task_id,
        "status": state.status,
        "updated_at": state.updated_at,
    }
