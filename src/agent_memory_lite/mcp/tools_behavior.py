"""Behavior-instruction + usage-feedback MCP tool handlers."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.ingestion.behavior_writer import upsert_behavior_instruction
from agent_memory_lite.maintenance.usage_feedback import record_usage_feedback
from agent_memory_lite.mcp.tools_payloads import behavior_instruction_payload
from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.repositories.behavior_repo import list_behavior_instructions


def memory_record_usage_feedback(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    feedback = record_usage_feedback(
        conn,
        workspace_id=str(payload.get("workspace_id", "default")),
        source_type=str(payload.get("source_type", "chunk")),
        source_id=str(payload["source_id"]),
        query=str(payload.get("query", "")),
        usefulness=float(payload["usefulness"]),
        task_id=payload.get("task_id"),
        notes=str(payload.get("notes", "")),
    )
    return feedback.to_dict()


def memory_upsert_behavior_instruction(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    instruction = upsert_behavior_instruction(conn, BehaviorInstructionIn(**payload))
    return behavior_instruction_payload(instruction)


def memory_list_behavior_instructions(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    kinds: list[str] | None = None,
    include_inactive: bool = False,
    limit: int = 10,
    since: str | None = None,
    until: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    from agent_memory_lite.models.enums import BehaviorInstructionKind  # noqa: PLC0415

    parsed_kinds = [BehaviorInstructionKind(kind) for kind in kinds] if kinds else None
    return {
        "instructions": [
            behavior_instruction_payload(item)
            for item in list_behavior_instructions(
                conn,
                workspace_id=workspace_id,
                query=query,
                kinds=parsed_kinds,
                include_inactive=include_inactive,
                limit=limit,
                since=since,
                until=until,
            )
        ]
    }
