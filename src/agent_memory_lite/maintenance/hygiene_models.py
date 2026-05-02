"""Result types + small helpers for the memory hygiene report.

The stopword set lives in ``hygiene_stopwords.py`` so this module
stays under the SLOC ceiling.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_memory_lite.maintenance.hygiene_stopwords import STOPWORDS

_TOKEN_RE = re.compile(r"\w+(?:[-.]\w+)*", re.UNICODE)


@dataclass(frozen=True, slots=True)
class HygieneFinding:
    kind: str
    severity: str
    target_type: str
    target_id: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "summary": self.summary,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class HygieneReport:
    status: str
    workspace_id: str
    generated_at: str
    counts: dict[str, int]
    findings: list[HygieneFinding]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "generated_at": self.generated_at,
            "counts": self.counts,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'virtual table')",
        (table,),
    ).fetchone()
    return row is not None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def json_len(raw: str | None) -> int:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return 0
    if isinstance(data, list | dict):
        return len(data)
    return 0


def json_text(raw: str | None) -> str:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return ""

    def _flatten(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str | int | float | bool):
            return [str(value)]
        if isinstance(value, list):
            items: list[str] = []
            for item in value:
                items.extend(_flatten(item))
            return items
        if isinstance(value, dict):
            items = []
            for key, item in value.items():
                items.append(str(key))
                items.extend(_flatten(item))
            return items
        return [str(value)]

    return " ".join(_flatten(data))


def tokens_from(text: str | None) -> set[str]:
    if not text:
        return set()
    return {
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if len(token) > 2 and token.lower() not in STOPWORDS
    }
