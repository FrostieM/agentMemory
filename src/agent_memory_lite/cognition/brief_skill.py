"""Skill body fetch — used by memory_invoke_skill, separate from the brief.

Extracted from cognition/brief.py during the v3.7 SLOC decomposition.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.storage.reader import get_object


def fetch_skill_body(
    conn: sqlite3.Connection, *, workspace_id: str, skill_id: str
) -> dict[str, Any] | None:
    """Return full skill row with body_md. Used by memory_invoke_skill only.

    Bumps usage_count + last_invoked_at as a side effect — the invoke
    counts as one use.
    """
    obj = get_object(
        conn,
        workspace_id=workspace_id,
        kind="skill",
        object_id=skill_id,
        fields=["body_md", "summary"],
    )
    if obj is None:
        return None
    conn.execute(
        "UPDATE skills SET usage_count = usage_count + 1, "
        "last_invoked_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
        "WHERE workspace_id = ? AND id = ?",
        (workspace_id, skill_id),
    )
    conn.commit()
    return obj
