"""Search-side queries for capability links.

Split out of ``capability_links_repo.py`` so the repo file stays under
the SLOC ceiling. The repo file owns CRUD; this module owns the
read-side fan-out queries used by hygiene reports and retrieval.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from agent_memory_lite.models.capability_links import CapabilityLink
from agent_memory_lite.models.enums import (
    CapabilityLinkRelation,
    CapabilityLinkTargetType,
    CapabilityType,
    coerce_enum,
)


def _row_to_link(row: sqlite3.Row) -> CapabilityLink:
    # v3.5 audit-followup: three drift sites in one parser. Capability
    # links enrich list_decisions / list_theories on every brief and
    # envelope; an unknown relation / target_type / capability_type would
    # 500 the entire route. Fallbacks: DECISION (most common target),
    # SKILL (most common capability), METHOD (the generic relation).
    return CapabilityLink(
        id=row["id"],
        workspace_id=row["workspace_id"],
        target_type=coerce_enum(
            CapabilityLinkTargetType,
            row["target_type"],
            CapabilityLinkTargetType.DECISION,
        ),
        target_id=row["target_id"],
        capability_type=coerce_enum(CapabilityType, row["capability_type"], CapabilityType.SKILL),
        capability_id=row["capability_id"],
        capability_name=row["capability_name"],
        relation=coerce_enum(
            CapabilityLinkRelation, row["relation"], CapabilityLinkRelation.METHOD
        ),
        rationale=row["rationale"],
        strength=float(row["strength"]),
        source_episode_id=row["source_episode_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


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


# Used by capability_links_repo for single-row lookups.
row_to_link = _row_to_link
