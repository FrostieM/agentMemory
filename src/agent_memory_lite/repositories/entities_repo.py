"""SQL operations for the `entities` table."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.entities import Entity


def _row_to_entity(row: sqlite3.Row) -> Entity:
    return Entity(
        id=row["id"],
        workspace_id=row["workspace_id"],
        type=row["type"],
        canonical_name=row["canonical_name"],
        aliases=json.loads(row["aliases_json"] or "[]"),
        properties=json.loads(row["properties_json"] or "{}"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def insert_entity_row(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    workspace_id: str,
    entity_type: str,
    canonical_name: str,
    aliases: list[str],
    properties: dict[str, object],
    timestamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO entities (
            id, workspace_id, type, canonical_name,
            aliases_json, properties_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_id,
            workspace_id,
            entity_type,
            canonical_name,
            json.dumps(aliases, sort_keys=True),
            json.dumps(properties, sort_keys=True),
            timestamp,
            timestamp,
        ),
    )


def update_entity_meta(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    aliases: list[str],
    properties: dict[str, object],
    timestamp: str,
) -> None:
    conn.execute(
        """
        UPDATE entities
        SET aliases_json = ?, properties_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            json.dumps(aliases, sort_keys=True),
            json.dumps(properties, sort_keys=True),
            timestamp,
            entity_id,
        ),
    )


def get_entity(conn: sqlite3.Connection, entity_id: str) -> Entity | None:
    row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
    return _row_to_entity(row) if row is not None else None


def find_entity_by_name(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    entity_type: str,
    canonical_name: str,
) -> Entity | None:
    row = conn.execute(
        """
        SELECT * FROM entities
        WHERE workspace_id = ? AND type = ? AND canonical_name = ?
        """,
        (workspace_id, entity_type, canonical_name),
    ).fetchone()
    return _row_to_entity(row) if row is not None else None


def list_entities(conn: sqlite3.Connection, workspace_id: str) -> list[Entity]:
    rows = conn.execute(
        "SELECT * FROM entities WHERE workspace_id = ? ORDER BY canonical_name",
        (workspace_id,),
    ).fetchall()
    return [_row_to_entity(r) for r in rows]
