"""Process-stage view + signature for the observatory UI."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from agent_memory_lite.api.routes.ui_db import clip, latest_rows, row_id, row_status, row_time
from agent_memory_lite.api.routes.ui_specs import PROCESS_EDGES, PROCESS_STAGES, TABLE_TO_STAGE


def _event_label(table: str, row: sqlite3.Row, fallback: str) -> str:
    keys = set(row.keys())
    if "label" in keys and row["label"]:
        return clip(row["label"], 110)
    for key in ("title", "name", "summary", "goal", "path", "raw_text", "text", "evidence"):
        if key in keys and row[key]:
            return clip(row[key], 110)
    return fallback


def _stage_latest_event(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    tables: list[str],
) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    for table in tables:
        rows = latest_rows(conn, table, workspace_id=workspace_id, limit=1)
        if not rows:
            continue
        row = rows[0]
        time_value = row_time(row) or ""
        event = {
            "id": row_id(row),
            "table": table,
            "label": _event_label(table, row, row_id(row)),
            "status": row_status(row),
            "updated_at": time_value or None,
        }
        if latest is None or time_value > str(latest.get("updated_at") or ""):
            latest = event
    return latest


def build_process(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    counts: dict[str, int],
    recent: list[dict[str, Any]],
) -> dict[str, Any]:
    stages: list[dict[str, Any]] = []
    for stage in PROCESS_STAGES:
        tables = list(stage["tables"])
        total = sum(counts.get(table, 0) for table in tables)
        latest = _stage_latest_event(conn, workspace_id=workspace_id, tables=tables)
        status = "empty" if total == 0 else "active"
        if stage["id"] == "governance" and counts.get("maintenance_events", 0):
            status = "review"
        stages.append(
            {
                "id": stage["id"],
                "label": stage["label"],
                "verb": stage["verb"],
                "tables": tables,
                "count": total,
                "status": status,
                "latest": latest,
            }
        )

    events = [{**event, "stage": TABLE_TO_STAGE.get(event["table"], "capture")} for event in recent]
    return {"stages": stages, "edges": PROCESS_EDGES, "events": events}


def signature(counts: dict[str, int], recent: list[dict[str, Any]]) -> str:
    raw = repr(
        (
            sorted(counts.items()),
            [(item["table"], item["id"], item.get("updated_at")) for item in recent[:25]],
        )
    )
    return hashlib.blake2s(raw.encode("utf-8"), digest_size=8).hexdigest()
