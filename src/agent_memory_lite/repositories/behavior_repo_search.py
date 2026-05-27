"""Search-side queries for behavior instructions.

Ranking + filtering helpers live in ``behavior_repo_ranking.py``.
This module owns the two list endpoints (active + suppressed) and the
``SuppressedBehaviorInstruction`` dataclass.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.models.behavior import BehaviorInstruction
from agent_memory_lite.models.enums import BehaviorInstructionKind
from agent_memory_lite.repositories.behavior_repo_ranking import (
    behavior_instruction_expired,
    contains_any,
    instruction_text,
    rank_instruction,
    row_to_instruction,
    tokens_from,
)
from agent_memory_lite.utils.sql_filters import date_range_clause

# Re-export so existing imports `from ...behavior_repo_search import row_to_instruction`
# keep working after the helper move.
__all__ = [
    "SuppressedBehaviorInstruction",
    "behavior_instruction_expired",
    "list_behavior_instructions",
    "list_suppressed_behavior_instructions",
    "row_to_instruction",
]


@dataclass(frozen=True, slots=True)
class SuppressedBehaviorInstruction:
    id: str
    name: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


def list_behavior_instructions(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    kinds: list[BehaviorInstructionKind] | None = None,
    include_inactive: bool = False,
    include_unmatched: bool = False,
    limit: int = 20,
    since: str | None = None,
    until: str | None = None,
) -> list[BehaviorInstruction]:
    extra_sql, extra_params = date_range_clause(since=since, until=until)
    rows = conn.execute(
        f"SELECT * FROM behaviors WHERE workspace_id = ? {extra_sql}",
        (workspace_id, *extra_params),
    ).fetchall()
    terms = tokens_from(query)
    instructions = [row_to_instruction(row) for row in rows]
    if kinds is not None:
        allowed = set(kinds)
        instructions = [item for item in instructions if item.kind in allowed]
    if not include_inactive:
        instructions = [item for item in instructions if item.active]
        instructions = [item for item in instructions if not behavior_instruction_expired(item)]
    if not include_unmatched:
        instructions = [
            item for item in instructions if contains_any(instruction_text(item), terms)
        ]
    instructions.sort(key=lambda item: rank_instruction(item, terms), reverse=True)
    return instructions[:limit]


def list_suppressed_behavior_instructions(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    kinds: list[BehaviorInstructionKind] | None = None,
    limit: int = 20,
) -> list[SuppressedBehaviorInstruction]:
    rows = conn.execute(
        "SELECT * FROM behaviors WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    terms = tokens_from(query)
    allowed = set(kinds) if kinds is not None else None
    suppressed: list[SuppressedBehaviorInstruction] = []
    for item in [row_to_instruction(row) for row in rows]:
        reason = ""
        details: dict[str, Any] = {}
        if allowed is not None and item.kind not in allowed:
            reason = "kind_filtered"
            details["kind"] = item.kind.value
        elif not item.active:
            reason = "inactive"
        elif behavior_instruction_expired(item):
            reason = "expired"
            details["expires_at"] = item.expires_at
        elif terms and not contains_any(instruction_text(item), terms):
            reason = "query_mismatch"
        if reason:
            suppressed.append(
                SuppressedBehaviorInstruction(
                    id=item.id,
                    name=item.name,
                    reason=reason,
                    details=details,
                )
            )
    return suppressed[:limit]
