"""SQL operations for persistent behavior instructions."""

from __future__ import annotations

import json
import re
import sqlite3

from agent_memory_lite.models.behavior import BehaviorInstruction, BehaviorInstructionSet
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)

_PRIORITY_WEIGHT = {
    BehaviorInstructionPriority.SYSTEM_BOUND: 4.0,
    BehaviorInstructionPriority.USER_PREFERENCE: 3.0,
    BehaviorInstructionPriority.PROJECT_CONVENTION: 2.0,
    BehaviorInstructionPriority.SUGGESTION: 1.0,
}
_SCOPE_WEIGHT = {
    BehaviorInstructionScope.TASK: 5.0,
    BehaviorInstructionScope.PROJECT: 4.0,
    BehaviorInstructionScope.WORKSPACE: 3.0,
    BehaviorInstructionScope.ROLE: 2.0,
    BehaviorInstructionScope.GLOBAL: 1.0,
}


def _json_list(raw: str | None) -> list[str]:
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _tokens(query: str | None) -> list[str]:
    if not query:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(query) if len(token) > 1]


def _row_to_instruction(row: sqlite3.Row) -> BehaviorInstruction:
    return BehaviorInstruction(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        kind=BehaviorInstructionKind(row["kind"]),
        scope=BehaviorInstructionScope(row["scope"]),
        priority=BehaviorInstructionPriority(row["priority"]),
        rule=row["rule"],
        rationale=row["rationale"] or "",
        applies_to=_json_list(row["applies_to_json"]),
        conflict_policy=BehaviorConflictPolicy(row["conflict_policy"]),
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _instruction_text(item: BehaviorInstruction) -> str:
    return " ".join(
        [
            item.name,
            item.kind.value,
            item.scope.value,
            item.priority.value,
            item.rule,
            item.rationale,
            " ".join(item.applies_to),
            item.conflict_policy.value,
        ]
    )


def _contains_any(text: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    lower = text.lower()
    return any(token in lower for token in tokens)


def _rank_instruction(item: BehaviorInstruction, tokens: list[str]) -> tuple[float, str]:
    text = _instruction_text(item).lower()
    token_score = sum(1.0 for token in tokens if token in text)
    active_bonus = 0.25 if item.active else -0.25
    score = (
        token_score
        + _PRIORITY_WEIGHT[item.priority]
        + (_SCOPE_WEIGHT[item.scope] * 0.1)
        + item.confidence
        + active_bonus
    )
    return score, item.updated_at


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
            active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, name) DO UPDATE SET
            kind = excluded.kind,
            scope = excluded.scope,
            priority = excluded.priority,
            rule = excluded.rule,
            rationale = excluded.rationale,
            applies_to_json = excluded.applies_to_json,
            conflict_policy = excluded.conflict_policy,
            source_episode_id = excluded.source_episode_id,
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
    return _row_to_instruction(row) if row is not None else None


def list_behavior_instructions(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    kinds: list[BehaviorInstructionKind] | None = None,
    include_inactive: bool = False,
    include_unmatched: bool = False,
    limit: int = 20,
) -> list[BehaviorInstruction]:
    rows = conn.execute(
        "SELECT * FROM behavior_instructions WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    terms = _tokens(query)
    instructions = [_row_to_instruction(row) for row in rows]
    if kinds is not None:
        allowed = set(kinds)
        instructions = [item for item in instructions if item.kind in allowed]
    if not include_inactive:
        instructions = [item for item in instructions if item.active]
    if not include_unmatched:
        instructions = [
            item for item in instructions if _contains_any(_instruction_text(item), terms)
        ]
    instructions.sort(key=lambda item: _rank_instruction(item, terms), reverse=True)
    return instructions[:limit]


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
