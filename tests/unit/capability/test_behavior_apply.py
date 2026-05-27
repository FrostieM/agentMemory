"""Behavior-instruction application_count tracking is opt-in but, when on,
reliably advances the counter and writes exactly one audit row per batch."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.capability.behavior_apply import mark_behavior_instructions_applied


def _seed_instruction(conn: sqlite3.Connection, *, instruction_id: str) -> None:
    conn.execute(
        """
        INSERT INTO behaviors
        (id, workspace_id, name, kind, scope, priority, rule, rationale,
         applies_to_json, conflict_policy, source_episode_id, confidence,
         active, created_at, updated_at, source_type, source_id, reviewed_by,
         reviewed_at, expires_at, last_applied_at, application_count,
         conflict_group, pinned)
        VALUES (?, 'default', ?, 'operating_rule', 'workspace',
                'user_preference', 'rule body', '', '[]',
                'current_user_wins', NULL, 0.9, 1,
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
                'manual', NULL, NULL, NULL, NULL, NULL, 0, NULL, 0)
        """,
        (instruction_id, f"name-{instruction_id}"),
    )


def _read(conn: sqlite3.Connection, instruction_id: str) -> tuple[int, str | None]:
    row = conn.execute(
        "SELECT application_count, last_applied_at FROM behaviors WHERE id = ?",
        (instruction_id,),
    ).fetchone()
    return int(row[0]), (str(row[1]) if row[1] else None)


def test_empty_id_list_is_a_noop(applied_conn: sqlite3.Connection) -> None:
    assert (
        mark_behavior_instructions_applied(applied_conn, workspace_id="default", instruction_ids=[])
        == 0
    )


def test_single_instruction_advances_and_audits(applied_conn: sqlite3.Connection) -> None:
    _seed_instruction(applied_conn, instruction_id="bi_a")
    updated = mark_behavior_instructions_applied(
        applied_conn, workspace_id="default", instruction_ids=["bi_a"]
    )
    assert updated == 1
    count, applied_at = _read(applied_conn, "bi_a")
    assert count == 1
    assert applied_at is not None

    audit_actions = applied_conn.execute(
        "SELECT action FROM audit_log WHERE target_type = 'behavior_instruction'"
    ).fetchall()
    assert [str(row[0]) for row in audit_actions] == ["instruction.applied"]


def test_batch_writes_one_audit_row_only(applied_conn: sqlite3.Connection) -> None:
    for instruction_id in ("bi_a", "bi_b", "bi_c"):
        _seed_instruction(applied_conn, instruction_id=instruction_id)
    updated = mark_behavior_instructions_applied(
        applied_conn, workspace_id="default", instruction_ids=["bi_a", "bi_b", "bi_c"]
    )
    assert updated == 3
    audit_rows = applied_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'instruction.applied'"
    ).fetchone()
    assert int(audit_rows[0]) == 1


def test_unknown_ids_skipped_no_audit(applied_conn: sqlite3.Connection) -> None:
    updated = mark_behavior_instructions_applied(
        applied_conn, workspace_id="default", instruction_ids=["bi_missing"]
    )
    assert updated == 0
    audit_rows = applied_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'instruction.applied'"
    ).fetchone()
    assert int(audit_rows[0]) == 0


def test_repeated_call_increments_count_each_time(applied_conn: sqlite3.Connection) -> None:
    _seed_instruction(applied_conn, instruction_id="bi_a")
    mark_behavior_instructions_applied(
        applied_conn, workspace_id="default", instruction_ids=["bi_a"]
    )
    mark_behavior_instructions_applied(
        applied_conn, workspace_id="default", instruction_ids=["bi_a"]
    )
    count, _ = _read(applied_conn, "bi_a")
    assert count == 2
