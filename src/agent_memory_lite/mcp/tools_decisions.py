"""Decision + task-state MCP tool handlers."""

from __future__ import annotations

import sqlite3
from typing import Any

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
    decision = write_decision(conn, DecisionIn(**payload))
    return {"decision_id": decision.id, "status": decision.status.value}


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
