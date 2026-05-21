"""Capability + target lookup helpers.

Split out of ``capability_links_repo.py`` so the repo file stays under
the SLOC ceiling. ``resolve_capability`` finds a capability by id or
name; ``resolve_target_workspace`` returns the workspace_id of a
research target.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.enums import CapabilityLinkTargetType, CapabilityType

CAPABILITY_TABLES: dict[CapabilityType, str] = {
    CapabilityType.ROLE: "agent_roles",
    CapabilityType.SKILL: "agent_skills",
    CapabilityType.PLAYBOOK: "agent_playbooks",
}

TARGET_TABLES: dict[CapabilityLinkTargetType, str] = {
    CapabilityLinkTargetType.THEORY: "theories",
    CapabilityLinkTargetType.THEORY_EVIDENCE: "theory_evidence",
    CapabilityLinkTargetType.EXPERIMENT: "research_experiments",
    CapabilityLinkTargetType.EXPERIMENT_RESULT: "experiment_results",
    CapabilityLinkTargetType.RESEARCH_INSIGHT: "research_insights",
    CapabilityLinkTargetType.MEMORY_CANDIDATE: "memory_candidates",
    CapabilityLinkTargetType.DECISION: "decisions",
    CapabilityLinkTargetType.PLAN_STEP: "plan_steps",
}


def resolve_capability(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    capability_type: CapabilityType,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> tuple[str, str] | None:
    table = CAPABILITY_TABLES[capability_type]
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
    table = TARGET_TABLES[target_type]
    row = conn.execute(f"SELECT workspace_id FROM {table} WHERE id = ?", (target_id,)).fetchone()
    return str(row["workspace_id"]) if row is not None else None
