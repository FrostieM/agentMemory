"""Decision + task-state MCP tool handlers."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.ingestion._write_helpers import (
    capability_suggestion_dicts,
    resolve_source_episode_id,
)
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.task_state_writer import write_task_state
from agent_memory_lite.mcp.tools_payloads import decision_payload
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.task_state import TaskStateIn
from agent_memory_lite.repositories.decisions_repo import (
    list_active_decisions,
    list_all_decisions,
)


def memory_write_decision(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    """In-process MCP handler. Mirrors the HTTP route shape: applies
    Move 1 (auto-thread source_episode_id) and Move 3
    (capability_suggestions). Settings are looked up lazily inside the
    helper so this signature stays compatible with ``dispatch()``.
    """
    workspace_id = str(payload.get("workspace_id") or "default")
    allow_orphan = bool(payload.pop("allow_orphan", False))
    payload["source_episode_id"] = resolve_source_episode_id(
        conn,
        workspace_id=workspace_id,
        explicit=payload.get("source_episode_id"),
        allow_orphan=allow_orphan,
    )
    decision = write_decision(conn, DecisionIn(**payload))
    return {
        "decision_id": decision.id,
        "status": decision.status.value,
        "source_episode_id": decision.source_episode_id,
        "capability_suggestions": capability_suggestion_dicts(
            conn,
            workspace_id=workspace_id,
            title=str(payload.get("title") or ""),
            text=str(payload.get("decision_text") or ""),
            rationale=payload.get("rationale"),
        ),
    }


def memory_list_decisions(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    include_superseded: bool = False,
    limit: int = 10,
    **_kwargs: Any,
) -> dict[str, Any]:
    if include_superseded:
        decisions = list_all_decisions(conn, workspace_id, query=query, limit=limit)
    else:
        decisions = list_active_decisions(conn, workspace_id, query=query, limit=limit)
    return {"decisions": [decision_payload(item) for item in decisions]}


def memory_update_task_state(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    state = write_task_state(conn, TaskStateIn(**payload))
    return {"state_id": state.id, "task_id": state.task_id, "status": state.status}
