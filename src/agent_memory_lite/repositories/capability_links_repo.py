"""SQL operations for links between capabilities and research objects.

Resolver helpers (``resolve_capability``, ``resolve_target_workspace``,
plus the capability/target table maps) live in
``capability_links_resolver.py``. Search and fan-out queries live in
``capability_links_search.py``. They are re-exported here so existing
import sites keep working.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.capability_links import CapabilityLink
from agent_memory_lite.models.enums import (
    CapabilityLinkRelation,
    CapabilityLinkTargetType,
    CapabilityType,
)
from agent_memory_lite.repositories.capability_links_resolver import (
    CAPABILITY_TABLES,
    TARGET_TABLES,
    resolve_capability,
    resolve_target_workspace,
)
from agent_memory_lite.repositories.capability_links_search import (
    capability_link_search_text,
    capability_link_text_by_target,
    list_capability_links,
    list_capability_links_for_targets,
    row_to_link,
)

__all__ = [
    "CAPABILITY_TABLES",
    "TARGET_TABLES",
    "capability_link_search_text",
    "capability_link_text_by_target",
    "get_capability_link",
    "get_capability_link_by_unique",
    "list_capability_links",
    "list_capability_links_for_targets",
    "resolve_capability",
    "resolve_target_workspace",
    "upsert_capability_link_row",
]


def upsert_capability_link_row(
    conn: sqlite3.Connection,
    *,
    link_id: str,
    workspace_id: str,
    target_type: CapabilityLinkTargetType,
    target_id: str,
    capability_type: CapabilityType,
    capability_id: str,
    capability_name: str,
    relation: CapabilityLinkRelation,
    rationale: str | None,
    strength: float,
    source_episode_id: str | None,
    created_at: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO capability_links (
            id, workspace_id, target_type, target_id, capability_type,
            capability_id, capability_name, relation, rationale, strength,
            source_episode_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(
            workspace_id, target_type, target_id, capability_type,
            capability_id, relation
        ) DO UPDATE SET
            capability_name = excluded.capability_name,
            rationale = excluded.rationale,
            strength = excluded.strength,
            source_episode_id = excluded.source_episode_id,
            updated_at = excluded.updated_at
        """,
        (
            link_id,
            workspace_id,
            target_type.value,
            target_id,
            capability_type.value,
            capability_id,
            capability_name,
            relation.value,
            rationale,
            strength,
            source_episode_id,
            created_at,
            updated_at,
        ),
    )


def get_capability_link(conn: sqlite3.Connection, link_id: str) -> CapabilityLink | None:
    row = conn.execute("SELECT * FROM capability_links WHERE id = ?", (link_id,)).fetchone()
    return row_to_link(row) if row is not None else None


def get_capability_link_by_unique(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    target_type: CapabilityLinkTargetType,
    target_id: str,
    capability_type: CapabilityType,
    capability_id: str,
    relation: CapabilityLinkRelation,
) -> CapabilityLink | None:
    row = conn.execute(
        """
        SELECT * FROM capability_links
        WHERE workspace_id = ?
          AND target_type = ?
          AND target_id = ?
          AND capability_type = ?
          AND capability_id = ?
          AND relation = ?
        """,
        (
            workspace_id,
            target_type.value,
            target_id,
            capability_type.value,
            capability_id,
            relation.value,
        ),
    ).fetchone()
    return row_to_link(row) if row is not None else None
