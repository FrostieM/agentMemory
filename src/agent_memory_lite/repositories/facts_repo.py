"""SQL operations for the `facts` table."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.enums import TrustLevel
from agent_memory_lite.models.facts import Fact


def _row_to_fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        workspace_id=row["workspace_id"],
        subject_entity_id=row["subject_entity_id"],
        relation=row["relation"],
        object_entity_id=row["object_entity_id"],
        literal_value=row["literal_value"],
        fact_text=row["fact_text"],
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        trust_level=TrustLevel(row["trust_level"]),
        observed_at=row["observed_at"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        invalidated_by_fact_id=row["invalidated_by_fact_id"],
        created_at=row["created_at"],
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def insert_fact_row(
    conn: sqlite3.Connection,
    *,
    fact_id: str,
    workspace_id: str,
    subject_entity_id: str,
    relation: str,
    object_entity_id: str | None,
    literal_value: str | None,
    fact_text: str,
    source_episode_id: str,
    confidence: float,
    importance: float,
    trust_level: TrustLevel,
    observed_at: str,
    valid_from: str,
    metadata: dict[str, object],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO facts (
            id, workspace_id, subject_entity_id, relation,
            object_entity_id, literal_value, fact_text, source_episode_id,
            confidence, importance, trust_level, observed_at, valid_from,
            valid_to, invalidated_by_fact_id, created_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
        """,
        (
            fact_id,
            workspace_id,
            subject_entity_id,
            relation,
            object_entity_id,
            literal_value,
            fact_text,
            source_episode_id,
            confidence,
            importance,
            trust_level.value,
            observed_at,
            valid_from,
            created_at,
            json.dumps(metadata, sort_keys=True),
        ),
    )


def close_fact(
    conn: sqlite3.Connection,
    *,
    fact_id: str,
    valid_to: str,
    invalidated_by_fact_id: str,
) -> None:
    conn.execute(
        """
        UPDATE facts
        SET valid_to = ?, invalidated_by_fact_id = ?
        WHERE id = ?
        """,
        (valid_to, invalidated_by_fact_id, fact_id),
    )


def get_fact(conn: sqlite3.Connection, fact_id: str) -> Fact | None:
    row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    return _row_to_fact(row) if row is not None else None


def find_open_facts(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    subject_entity_id: str,
    relation: str,
) -> list[Fact]:
    rows = conn.execute(
        """
        SELECT * FROM facts
        WHERE workspace_id = ? AND subject_entity_id = ? AND relation = ?
          AND valid_to IS NULL AND invalidated_by_fact_id IS NULL
        ORDER BY created_at DESC
        """,
        (workspace_id, subject_entity_id, relation),
    ).fetchall()
    return [_row_to_fact(r) for r in rows]


def list_active_facts(conn: sqlite3.Connection, workspace_id: str) -> list[Fact]:
    rows = conn.execute(
        """
        SELECT * FROM facts
        WHERE workspace_id = ?
          AND valid_to IS NULL AND invalidated_by_fact_id IS NULL
        ORDER BY importance DESC, created_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [_row_to_fact(r) for r in rows]


def list_all_facts_for_subjects(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    subject_ids: list[str],
    include_invalidated: bool = False,
) -> list[Fact]:
    if not subject_ids:
        return []
    marks = ",".join("?" * len(subject_ids))
    where_clause = (
        "" if include_invalidated else (" AND valid_to IS NULL AND invalidated_by_fact_id IS NULL")
    )
    sql = (
        f"SELECT * FROM facts "
        f"WHERE workspace_id = ? AND subject_entity_id IN ({marks})"
        f"{where_clause} ORDER BY importance DESC, created_at DESC"
    )
    rows = conn.execute(sql, (workspace_id, *subject_ids)).fetchall()
    return [_row_to_fact(r) for r in rows]
