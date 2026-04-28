"""SQL operations for links between capabilities and research objects."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from agent_memory_lite.models.capability_links import CapabilityLink
from agent_memory_lite.models.enums import (
    CapabilityLinkRelation,
    CapabilityLinkTargetType,
    CapabilityType,
)

_CAPABILITY_TABLES: dict[CapabilityType, str] = {
    CapabilityType.ROLE: "agent_roles",
    CapabilityType.SKILL: "agent_skills",
    CapabilityType.PLAYBOOK: "agent_playbooks",
}

_TARGET_TABLES: dict[CapabilityLinkTargetType, str] = {
    CapabilityLinkTargetType.THEORY: "theories",
    CapabilityLinkTargetType.THEORY_EVIDENCE: "theory_evidence",
    CapabilityLinkTargetType.EXPERIMENT: "research_experiments",
    CapabilityLinkTargetType.EXPERIMENT_RESULT: "experiment_results",
    CapabilityLinkTargetType.RESEARCH_INSIGHT: "research_insights",
    CapabilityLinkTargetType.MEMORY_CANDIDATE: "memory_candidates",
    CapabilityLinkTargetType.DECISION: "decisions",
}


def _row_to_link(row: sqlite3.Row) -> CapabilityLink:
    return CapabilityLink(
        id=row["id"],
        workspace_id=row["workspace_id"],
        target_type=CapabilityLinkTargetType(row["target_type"]),
        target_id=row["target_id"],
        capability_type=CapabilityType(row["capability_type"]),
        capability_id=row["capability_id"],
        capability_name=row["capability_name"],
        relation=CapabilityLinkRelation(row["relation"]),
        rationale=row["rationale"],
        strength=float(row["strength"]),
        source_episode_id=row["source_episode_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def resolve_capability(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    capability_type: CapabilityType,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> tuple[str, str] | None:
    table = _CAPABILITY_TABLES[capability_type]
    if capability_id:
        row = conn.execute(
            f"SELECT id, name FROM {table} WHERE workspace_id = ? AND id = ?",
            (workspace_id, capability_id),
        ).fetchone()
    elif capability_name:
        row = conn.execute(
            f"SELECT id, name FROM {table} WHERE workspace_id = ? AND name = ?",
            (workspace_id, capability_name),
        ).fetchone()
    else:
        return None
    if row is None:
        return None
    return str(row["id"]), str(row["name"])


def resolve_target_workspace(
    conn: sqlite3.Connection,
    *,
    target_type: CapabilityLinkTargetType,
    target_id: str,
) -> str | None:
    table = _TARGET_TABLES[target_type]
    row = conn.execute(f"SELECT workspace_id FROM {table} WHERE id = ?", (target_id,)).fetchone()
    return str(row["workspace_id"]) if row is not None else None


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
    return _row_to_link(row) if row is not None else None


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
    return _row_to_link(row) if row is not None else None


def list_capability_links(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    target_type: CapabilityLinkTargetType | None = None,
    target_id: str | None = None,
    capability_type: CapabilityType | None = None,
    capability_id: str | None = None,
    limit: int = 50,
) -> list[CapabilityLink]:
    clauses = ["workspace_id = ?"]
    params: list[str | int | float] = [workspace_id]
    if target_type is not None:
        clauses.append("target_type = ?")
        params.append(target_type.value)
    if target_id is not None:
        clauses.append("target_id = ?")
        params.append(target_id)
    if capability_type is not None:
        clauses.append("capability_type = ?")
        params.append(capability_type.value)
    if capability_id is not None:
        clauses.append("capability_id = ?")
        params.append(capability_id)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT * FROM capability_links
        WHERE {" AND ".join(clauses)}
        ORDER BY strength DESC, updated_at DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_row_to_link(row) for row in rows]


def list_capability_links_for_targets(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    target_type: CapabilityLinkTargetType,
    target_ids: Iterable[str],
    limit_per_target: int = 5,
) -> dict[str, list[CapabilityLink]]:
    ids = list(dict.fromkeys(target_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT * FROM capability_links
        WHERE workspace_id = ?
          AND target_type = ?
          AND target_id IN ({placeholders})
        ORDER BY target_id, strength DESC, updated_at DESC
        """,
        (workspace_id, target_type.value, *ids),
    ).fetchall()
    grouped: dict[str, list[CapabilityLink]] = {target_id: [] for target_id in ids}
    for row in rows:
        link = _row_to_link(row)
        bucket = grouped.setdefault(link.target_id, [])
        if len(bucket) < limit_per_target:
            bucket.append(link)
    return grouped


def capability_link_search_text(link: CapabilityLink) -> str:
    return " ".join(
        [
            link.capability_name,
            link.capability_type.value,
            link.relation.value,
            link.rationale or "",
        ]
    )


def capability_link_text_by_target(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    target_type: CapabilityLinkTargetType,
    target_ids: Iterable[str],
) -> dict[str, str]:
    grouped = list_capability_links_for_targets(
        conn,
        workspace_id=workspace_id,
        target_type=target_type,
        target_ids=target_ids,
    )
    return {
        target_id: " ".join(capability_link_search_text(link) for link in links)
        for target_id, links in grouped.items()
    }
