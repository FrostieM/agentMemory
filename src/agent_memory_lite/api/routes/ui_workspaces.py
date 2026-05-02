"""Workspace listing helpers for the observatory UI."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.api.routes.ui_db import columns, quote_ident
from agent_memory_lite.api.routes.ui_specs import TABLES
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.config.workspace_registry import WorkspaceRegistry


def available_workspaces(conn: sqlite3.Connection, settings: Settings) -> list[str]:
    if settings.strict_workspace_isolation and not settings.hub_mode:
        return [settings.workspace_id]
    workspaces = {settings.workspace_id}
    for spec in TABLES:
        table = spec["table"]
        cols = columns(conn, table)
        if "workspace_id" not in cols:
            continue
        rows = conn.execute(
            f"""
            SELECT DISTINCT workspace_id
            FROM {quote_ident(table)}
            WHERE workspace_id IS NOT NULL AND workspace_id != ''
            ORDER BY workspace_id
            LIMIT 50
            """
        ).fetchall()
        workspaces.update(str(row[0]) for row in rows if row[0])
    if settings.hub_mode:
        for entry in WorkspaceRegistry(settings.workspaces_file).list():
            workspaces.add(entry.id)
    if settings.forbid_default_workspace:
        workspaces.discard("default")
    return sorted(workspaces)


def registered_workspaces(settings: Settings, current_workspace_id: str) -> list[dict[str, Any]]:
    """Hub-mode registry view: every workspace with its physical DB paths."""
    registry = WorkspaceRegistry(settings.workspaces_file)
    seen: dict[str, dict[str, Any]] = {}
    for entry in registry.list():
        seen[entry.id] = {
            "id": entry.id,
            "label": entry.label or entry.id,
            "db_path": entry.db_path,
            "vector_path": entry.vector_path,
            "project_root": entry.project_root,
            "registered_at": entry.registered_at,
            "last_seen_at": entry.last_seen_at,
            "is_current": entry.id == current_workspace_id,
        }
    if settings.workspace_id and settings.workspace_id not in seen:
        seen[settings.workspace_id] = {
            "id": settings.workspace_id,
            "label": settings.workspace_id,
            "db_path": str(settings.db_path),
            "vector_path": str(settings.vector_db_path),
            "project_root": "",
            "registered_at": "",
            "last_seen_at": "",
            "is_current": settings.workspace_id == current_workspace_id,
        }
    return sorted(seen.values(), key=lambda item: str(item["id"]))
