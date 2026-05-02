"""Ranking + filtering helpers for behavior instructions.

Split out of ``behavior_repo_search.py`` so each module stays under
the SLOC ceiling.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from agent_memory_lite.models.behavior import BehaviorInstruction
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)
from agent_memory_lite.utils.time import now, parse_iso

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)

PRIORITY_WEIGHT = {
    BehaviorInstructionPriority.SYSTEM_BOUND: 4.0,
    BehaviorInstructionPriority.USER_PREFERENCE: 3.0,
    BehaviorInstructionPriority.PROJECT_CONVENTION: 2.0,
    BehaviorInstructionPriority.SUGGESTION: 1.0,
}
SCOPE_WEIGHT = {
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


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except IndexError:
        return default


def tokens_from(query: str | None) -> list[str]:
    if not query:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(query) if len(token) > 1]


def row_to_instruction(row: sqlite3.Row) -> BehaviorInstruction:
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
        source_type=str(_row_value(row, "source_type", "manual") or "manual"),
        source_id=_row_value(row, "source_id"),
        reviewed_by=_row_value(row, "reviewed_by"),
        reviewed_at=_row_value(row, "reviewed_at"),
        expires_at=_row_value(row, "expires_at"),
        conflict_group=_row_value(row, "conflict_group"),
        confidence=float(row["confidence"]),
        active=bool(row["active"]),
        last_applied_at=_row_value(row, "last_applied_at"),
        application_count=int(_row_value(row, "application_count", 0) or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        pinned=bool(_row_value(row, "pinned", 0) or 0),
    )


def instruction_text(item: BehaviorInstruction) -> str:
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
            item.source_type,
            item.source_id or "",
            item.conflict_group or "",
        ]
    )


def contains_any(text: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    lower = text.lower()
    return any(token in lower for token in tokens)


def rank_instruction(item: BehaviorInstruction, tokens: list[str]) -> tuple[float, str]:
    text = instruction_text(item).lower()
    token_score = sum(1.0 for token in tokens if token in text)
    active_bonus = 0.25 if item.active else -0.25
    pin_bonus = 5.0 if item.pinned else 0.0
    score = (
        token_score
        + PRIORITY_WEIGHT[item.priority]
        + (SCOPE_WEIGHT[item.scope] * 0.1)
        + item.confidence
        + active_bonus
        + pin_bonus
    )
    return score, item.updated_at


def behavior_instruction_expired(item: BehaviorInstruction) -> bool:
    if not item.expires_at:
        return False
    try:
        return parse_iso(item.expires_at) <= now()
    except ValueError:
        return True
