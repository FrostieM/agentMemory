"""Regression: ``pinned`` flag must round-trip through row→model.

The earlier defensive check used ``"pinned" in row`` to decide
whether the column was present. On ``sqlite3.Row`` that operator
checks VALUES, not column names, so a row with ``pinned=1`` would
read as missing and the model would default to ``False``. The crash
test caught it via the UI: every decision came back as ``pinned:
False`` even after a successful POST /memory/pin.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)
from agent_memory_lite.repositories.behavior_repo import (
    upsert_behavior_instruction_row,
)
from agent_memory_lite.repositories.behavior_repo_ranking import row_to_instruction
from agent_memory_lite.repositories.decisions_repo import (
    _row_to_decision,
    insert_decision_row,
    set_decision_pinned,
)
from agent_memory_lite.utils.time import iso_now


def test_decision_pinned_roundtrips(applied_conn: sqlite3.Connection) -> None:
    workspace = "pin-row-ws"
    insert_decision_row(
        applied_conn,
        decision_id="dec_pin_a",
        workspace_id=workspace,
        title="Pinned decision",
        decision_text="Body.",
        rationale=None,
        supersedes_decision_id=None,
        source_episode_id=None,
        confidence=0.9,
        importance=0.9,
        valid_from=iso_now(),
        created_at=iso_now(),
    )
    set_decision_pinned(
        applied_conn,
        decision_id="dec_pin_a",
        workspace_id=workspace,
        pinned=True,
        updated_at=iso_now(),
    )
    applied_conn.commit()
    row = applied_conn.execute("SELECT * FROM decisions WHERE id = ?", ("dec_pin_a",)).fetchone()
    decision = _row_to_decision(row)
    assert decision.pinned is True


def test_behavior_pinned_roundtrips(applied_conn: sqlite3.Connection) -> None:
    workspace = "pin-row-behavior-ws"
    upsert_behavior_instruction_row(
        applied_conn,
        instruction_id="beh_pin_a",
        workspace_id=workspace,
        name="local_only",
        kind=BehaviorInstructionKind.OPERATING_RULE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.SYSTEM_BOUND,
        rule="Never call cloud LLMs.",
        rationale="",
        applies_to=[],
        conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
        source_episode_id=None,
        source_type="manual",
        source_id=None,
        reviewed_by=None,
        reviewed_at=None,
        expires_at=None,
        conflict_group=None,
        confidence=0.99,
        active=True,
        created_at=iso_now(),
        updated_at=iso_now(),
    )
    applied_conn.execute("UPDATE behaviors SET pinned = 1 WHERE id = ?", ("beh_pin_a",))
    applied_conn.commit()
    row = applied_conn.execute("SELECT * FROM behaviors WHERE id = ?", ("beh_pin_a",)).fetchone()
    behavior = row_to_instruction(row)
    assert behavior.pinned is True
