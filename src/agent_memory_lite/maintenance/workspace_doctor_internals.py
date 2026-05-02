"""Read helpers for ``workspace_doctor.py``.

Quarantine primitives (``write_quarantine``, ``quarantine_rows``,
``child_first_tables``) live in ``workspace_doctor_quarantine.py``.
This module owns table inspection, count aggregation, and sample-row
collection.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkspacePollutionRow:
    table: str
    workspace_id: str
    row_identity: str
    row: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "workspace_id": self.workspace_id,
            "row_identity": self.row_identity,
            "row": self.row,
        }


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'virtual table')",
        (table,),
    ).fetchone()
    return row is not None


def workspace_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type IN ('table', 'virtual table') AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    tables: list[str] = []
    for row in rows:
        table = str(row[0])
        try:
            cols = [str(col[1]) for col in conn.execute(f"PRAGMA table_info({quote_ident(table)})")]
        except sqlite3.OperationalError:
            continue
        if "workspace_id" in cols:
            tables.append(table)
    return tables


def pollution_condition() -> str:
    return "workspace_id IS NOT NULL AND workspace_id <> ?"


def quarantine_condition(*, include_default: bool) -> str:
    if include_default:
        return pollution_condition()
    return "workspace_id IS NOT NULL AND workspace_id NOT IN (?, 'default')"


def workspace_counts(conn: sqlite3.Connection, *, workspace_id: str) -> dict[str, dict[str, int]]:
    condition = pollution_condition()
    counts: dict[str, dict[str, int]] = {}
    for table in workspace_tables(conn):
        rows = conn.execute(
            f"""
            SELECT workspace_id, COUNT(*) AS n
            FROM {quote_ident(table)}
            WHERE {condition}
            GROUP BY workspace_id
            ORDER BY workspace_id
            """,
            (workspace_id,),
        ).fetchall()
        if rows:
            counts[table] = {str(row["workspace_id"]): int(row["n"]) for row in rows}
    return counts


def _row_identity(row: sqlite3.Row) -> str:
    keys = set(row.keys())
    for key in ("id", "chunk_id", "episode_id", "target_id", "task_id"):
        if key in keys and row[key] is not None:
            return str(row[key])
    return "<unknown>"


def sample_rows(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    limit_per_table: int,
) -> list[WorkspacePollutionRow]:
    condition = pollution_condition()
    samples: list[WorkspacePollutionRow] = []
    for table in workspace_tables(conn):
        rows = conn.execute(
            f"""
            SELECT *
            FROM {quote_ident(table)}
            WHERE {condition}
            ORDER BY workspace_id
            LIMIT ?
            """,
            (workspace_id, limit_per_table),
        ).fetchall()
        for row in rows:
            samples.append(
                WorkspacePollutionRow(
                    table=table,
                    workspace_id=str(row["workspace_id"]),
                    row_identity=_row_identity(row),
                    row=dict(row),
                )
            )
    return samples
