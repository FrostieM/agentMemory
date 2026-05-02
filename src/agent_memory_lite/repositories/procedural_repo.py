"""SQL operations for the `procedural_rules` table."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.procedural import ProceduralRule


def _row_to_rule(row: sqlite3.Row) -> ProceduralRule:
    return ProceduralRule(
        id=row["id"],
        workspace_id=row["workspace_id"],
        rule_text=row["rule_text"],
        scope=row["scope"],
        active=bool(row["active"]),
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def insert_procedural_rule_row(
    conn: sqlite3.Connection,
    *,
    rule_id: str,
    workspace_id: str,
    rule_text: str,
    scope: str,
    source_episode_id: str | None,
    confidence: float,
    importance: float,
    timestamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO procedural_rules (
            id, workspace_id, rule_text, scope, active,
            source_episode_id, confidence, importance, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
        """,
        (
            rule_id,
            workspace_id,
            rule_text,
            scope,
            source_episode_id,
            confidence,
            importance,
            timestamp,
            timestamp,
        ),
    )


def deactivate_rule(conn: sqlite3.Connection, *, rule_id: str, timestamp: str) -> int:
    cur = conn.execute(
        "UPDATE procedural_rules SET active = 0, updated_at = ? WHERE id = ?",
        (timestamp, rule_id),
    )
    return int(cur.rowcount)


def list_active_rules(conn: sqlite3.Connection, workspace_id: str) -> list[ProceduralRule]:
    rows = conn.execute(
        """
        SELECT * FROM procedural_rules
        WHERE workspace_id = ? AND active = 1
        ORDER BY importance DESC, updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [_row_to_rule(r) for r in rows]
