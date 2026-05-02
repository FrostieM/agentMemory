"""SQL operations for persistent behavior instructions.

Search, ranking, and the suppressed-instruction listing live in
``behavior_repo_search.py``. This module owns the upsert + by-id /
by-name lookups + the context-builder convenience.
"""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.behavior import BehaviorInstruction, BehaviorInstructionSet
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)
from agent_memory_lite.repositories.behavior_repo_ranking import (
    behavior_instruction_expired,
    row_to_instruction,
)
from agent_memory_lite.repositories.behavior_repo_search import (
    SuppressedBehaviorInstruction,
    list_behavior_instructions,
    list_suppressed_behavior_instructions,
)

__all__ = [
    "SuppressedBehaviorInstruction",
    "behavior_instruction_expired",
    "build_behavior_instruction_set",
    "get_behavior_instruction_by_id",
    "get_behavior_instruction_by_name",
    "list_behavior_instructions",
    "list_suppressed_behavior_instructions",
    "upsert_behavior_instruction_row",
]


def upsert_behavior_instruction_row(
    conn: sqlite3.Connection,
    *,
    instruction_id: str,
    workspace_id: str,
    name: str,
    kind: BehaviorInstructionKind,
    scope: BehaviorInstructionScope,
    priority: BehaviorInstructionPriority,
    rule: str,
    rationale: str,
    applies_to: list[str],
    conflict_policy: BehaviorConflictPolicy,
    source_episode_id: str | None,
    source_type: str,
    source_id: str | None,
    reviewed_by: str | None,
    reviewed_at: str | None,
    expires_at: str | None,
    conflict_group: str | None,
    confidence: float,
    active: bool,
    created_at: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO behavior_instructions (
            id, workspace_id, name, kind, scope, priority, rule, rationale,
            applies_to_json, conflict_policy, source_episode_id, confidence,
            active, source_type, source_id, reviewed_by, reviewed_at, expires_at,
            conflict_group, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, name) DO UPDATE SET
            kind = excluded.kind,
            scope = excluded.scope,
            priority = excluded.priority,
            rule = excluded.rule,
            rationale = excluded.rationale,
            applies_to_json = excluded.applies_to_json,
            conflict_policy = excluded.conflict_policy,
            source_episode_id = excluded.source_episode_id,
            source_type = excluded.source_type,
            source_id = excluded.source_id,
            reviewed_by = excluded.reviewed_by,
            reviewed_at = excluded.reviewed_at,
            expires_at = excluded.expires_at,
            conflict_group = excluded.conflict_group,
            confidence = excluded.confidence,
            active = excluded.active,
            updated_at = excluded.updated_at
        """,
        (
            instruction_id,
            workspace_id,
            name,
            kind.value,
            scope.value,
            priority.value,
            rule,
            rationale,
            json.dumps(applies_to, sort_keys=True),
            conflict_policy.value,
            source_episode_id,
            confidence,
            1 if active else 0,
            source_type,
            source_id,
            reviewed_by,
            reviewed_at,
            expires_at,
            conflict_group,
            created_at,
            updated_at,
        ),
    )


def get_behavior_instruction_by_name(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    name: str,
) -> BehaviorInstruction | None:
    row = conn.execute(
        "SELECT * FROM behavior_instructions WHERE workspace_id = ? AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return row_to_instruction(row) if row is not None else None


def get_behavior_instruction_by_id(
    conn: sqlite3.Connection, instruction_id: str
) -> BehaviorInstruction | None:
    row = conn.execute(
        "SELECT * FROM behavior_instructions WHERE id = ?", (instruction_id,)
    ).fetchone()
    return row_to_instruction(row) if row is not None else None


def build_behavior_instruction_set(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 6,
) -> BehaviorInstructionSet:
    return BehaviorInstructionSet(
        instructions=list_behavior_instructions(
            conn,
            workspace_id=workspace_id,
            query=query,
            include_inactive=include_inactive,
            include_unmatched=True,
            limit=limit,
        )
    )
