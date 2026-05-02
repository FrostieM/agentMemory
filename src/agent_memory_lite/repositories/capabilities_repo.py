"""Aggregator facade for agent capability memory.

Per-kind operations live in their own modules:
``capabilities_roles_repo.py``, ``capabilities_skills_repo.py``,
``capabilities_playbooks_repo.py``. This module re-exports their
public API and provides ``build_agent_capabilities`` which fetches all
three kinds at once for the retrieval pipeline.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.capabilities import AgentCapabilities
from agent_memory_lite.repositories.capabilities_playbooks_repo import (
    get_playbook_by_id,
    get_playbook_by_name,
    list_playbooks,
    upsert_playbook_row,
)
from agent_memory_lite.repositories.capabilities_roles_repo import (
    get_role_by_id,
    get_role_by_name,
    list_roles,
    upsert_role_row,
)
from agent_memory_lite.repositories.capabilities_skills_repo import (
    get_skill_by_id,
    get_skill_by_name,
    list_skills,
    upsert_skill_row,
)

__all__ = [
    "build_agent_capabilities",
    "get_playbook_by_id",
    "get_playbook_by_name",
    "get_role_by_id",
    "get_role_by_name",
    "get_skill_by_id",
    "get_skill_by_name",
    "list_playbooks",
    "list_roles",
    "list_skills",
    "upsert_playbook_row",
    "upsert_role_row",
    "upsert_skill_row",
]


def build_agent_capabilities(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 6,
) -> AgentCapabilities:
    per_kind_limit = max(1, limit)
    return AgentCapabilities(
        roles=list_roles(
            conn,
            workspace_id=workspace_id,
            query=query,
            include_inactive=include_inactive,
            limit=per_kind_limit,
        ),
        skills=list_skills(
            conn,
            workspace_id=workspace_id,
            query=query,
            include_inactive=include_inactive,
            limit=per_kind_limit,
        ),
        playbooks=list_playbooks(
            conn,
            workspace_id=workspace_id,
            query=query,
            include_inactive=include_inactive,
            limit=per_kind_limit,
        ),
    )
