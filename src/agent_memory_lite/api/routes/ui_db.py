"""SQL helpers for the observatory UI."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from agent_memory_lite.api.routes.ui_specs import TIME_COLUMNS


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'virtual table') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({quote_ident(table)})")}


def count_rows(conn: sqlite3.Connection, table: str, *, workspace_id: str) -> int:
    cols = columns(conn, table)
    if not cols:
        return 0
    if "workspace_id" in cols:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {quote_ident(table)} WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
    else:
        row = conn.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}").fetchone()
    return int(row[0] or 0)


def time_column(cols: set[str]) -> str | None:
    return next((column for column in TIME_COLUMNS if column in cols), None)


def latest_rows(
    conn: sqlite3.Connection,
    table: str,
    *,
    workspace_id: str,
    limit: int,
) -> list[sqlite3.Row]:
    cols = columns(conn, table)
    if not cols:
        return []
    time_col = time_column(cols)
    order = quote_ident(time_col) if time_col else "rowid"
    where = "WHERE workspace_id = ?" if "workspace_id" in cols else ""
    params: tuple[Any, ...] = (workspace_id, limit) if where else (limit,)
    return list(
        conn.execute(
            f"""
            SELECT *
            FROM {quote_ident(table)}
            {where}
            ORDER BY {order} DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    )


def clip(value: object, limit: int = 96) -> str:
    text = "" if value is None else str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "..."


def row_id(row: sqlite3.Row) -> str:
    keys = set(row.keys())
    for key in ("id", "chunk_id", "task_id", "source_id"):
        if key in keys and row[key] is not None:
            return str(row[key])
    return hashlib.blake2s(repr(dict(row)).encode("utf-8"), digest_size=8).hexdigest()


def row_time(row: sqlite3.Row) -> str | None:
    keys = set(row.keys())
    for key in TIME_COLUMNS:
        if key in keys and row[key]:
            return str(row[key])
    return None


def row_status(row: sqlite3.Row) -> str | None:
    keys = set(row.keys())
    for key in ("status", "kind", "source_type", "severity"):
        if key in keys and row[key]:
            return str(row[key])
    return None
