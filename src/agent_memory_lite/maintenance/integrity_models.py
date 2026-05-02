"""Result types + small helpers for the integrity audit."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)
ROUNDTRIP_STOPWORDS = {
    "about",
    "after",
    "against",
    "before",
    "candidate",
    "completed",
    "context",
    "decision",
    "default",
    "episode",
    "memory",
    "project",
    "reports",
    "status",
    "system",
    "theory",
    "workspace",
}


@dataclass(frozen=True, slots=True)
class IntegrityCheck:
    status: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    status: str
    workspace_id: str
    checks: dict[str, IntegrityCheck]
    counts: dict[str, Any]
    failures: list[str]
    warnings: list[str]
    repair_hints: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "checks": {
                name: {"status": check.status, "details": check.details}
                for name, check in self.checks.items()
            },
            "counts": self.counts,
            "failures": self.failures,
            "warnings": self.warnings,
            "repair_hints": self.repair_hints,
        }


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
            cols = [str(col[1]) for col in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        except sqlite3.OperationalError:
            continue
        if "workspace_id" in cols:
            tables.append(table)
    return tables


def count_query(conn: sqlite3.Connection, query: str, args: tuple[object, ...]) -> int:
    row = conn.execute(query, args).fetchone()
    return int(row[0]) if row else 0


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def sample_query(conn: sqlite3.Connection, workspace_id: str) -> tuple[str, list[str]] | None:
    row = conn.execute(
        """
        SELECT id, text
        FROM chunks
        WHERE workspace_id = ? AND length(text) > 0
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (workspace_id,),
    ).fetchone()
    if row is None:
        return None
    raw_tokens = [token for token in TOKEN_RE.findall(str(row["text"])) if len(token) >= 4]
    tokens = [
        token
        for token in raw_tokens
        if token.lower() not in ROUNDTRIP_STOPWORDS and not token.isdigit()
    ]
    tokens.sort(key=lambda token: (len(token), token), reverse=True)
    if len(tokens) < 3:
        tokens.extend(raw_tokens)
    tokens = list(dict.fromkeys(tokens))[:8]
    if not tokens:
        return None
    return str(row["id"]), tokens
