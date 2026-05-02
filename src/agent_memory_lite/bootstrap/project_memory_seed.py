"""Neutral seed data for newly created project memory databases.

This seed is intentionally narrow: it teaches the memory database how to be
populated, not how an agent should speak, code, or make project decisions.

The skill / playbook / concept payloads live in
``project_memory_seed_templates.py``; this file owns the orchestrator
that writes them and assembles the result.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from agent_memory_lite.bootstrap.project_memory_seed_templates import (
    memory_bootstrap_playbook,
    memory_population_skill,
    vocabulary_concepts,
)
from agent_memory_lite.ingestion.capability_writer import (
    upsert_agent_playbook,
    upsert_agent_skill,
)
from agent_memory_lite.ingestion.research_writer import upsert_domain_concept


@dataclass(frozen=True, slots=True)
class SeedObjectRef:
    kind: str
    id: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id, "name": self.name}


@dataclass(frozen=True, slots=True)
class ProjectMemorySeedResult:
    workspace_id: str
    profile: str
    skills: list[SeedObjectRef]
    playbooks: list[SeedObjectRef]
    concepts: list[SeedObjectRef]
    roles_written: int = 0
    behavior_instructions_written: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "profile": self.profile,
            "skills": [item.to_dict() for item in self.skills],
            "playbooks": [item.to_dict() for item in self.playbooks],
            "concepts": [item.to_dict() for item in self.concepts],
            "roles_written": self.roles_written,
            "behavior_instructions_written": self.behavior_instructions_written,
        }


PROFILE_NAME = "neutral-memory-bootstrap"


def seed_neutral_project_memory(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    source_episode_id: str | None = None,
) -> ProjectMemorySeedResult:
    """Seed neutral memory-population helpers into a project DB.

    The seed is idempotent because all written objects use upsert semantics on
    `(workspace_id, name)`. It deliberately avoids behavior instructions and
    roles so it cannot impose language, style, personality, or a project role on
    future agents.
    """

    skill = upsert_agent_skill(conn, memory_population_skill(workspace_id, source_episode_id))
    playbook = upsert_agent_playbook(
        conn, memory_bootstrap_playbook(workspace_id, source_episode_id)
    )
    concepts = [
        upsert_domain_concept(conn, payload)
        for payload in vocabulary_concepts(workspace_id, source_episode_id)
    ]

    return ProjectMemorySeedResult(
        workspace_id=workspace_id,
        profile=PROFILE_NAME,
        skills=[SeedObjectRef(kind="agent_skill", id=skill.id, name=skill.name)],
        playbooks=[
            SeedObjectRef(kind="agent_playbook", id=playbook.id, name=playbook.name),
        ],
        concepts=[
            SeedObjectRef(kind="domain_concept", id=concept.id, name=concept.name)
            for concept in concepts
        ],
    )
