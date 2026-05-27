"""Neutral seed data for newly created project memory databases.

This seed is intentionally narrow: it teaches the memory database how to be
populated, not how an agent should speak, code, or make project decisions.

The skill / playbook / concept payloads live in
``project_memory_seed_templates.py``; this file owns the orchestrator
that writes them and assembles the result.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.bootstrap.project_memory_seed_behavior import (
    PINNED_DISCIPLINE_FACTORIES,
)
from agent_memory_lite.bootstrap.project_memory_seed_templates import (
    DISCIPLINE_FACTORIES,
    memory_bootstrap_playbook,
    memory_population_skill,
    vocabulary_concepts,
)
from agent_memory_lite.ingestion.behavior_writer import upsert_behavior_instruction
from agent_memory_lite.ingestion.capability_writer import (
    upsert_agent_playbook,
    upsert_agent_skill,
)
from agent_memory_lite.ingestion.pin_service import pin_memory_object
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
    # Seed writes generic-discipline behaviors. Default empty list
    # keeps backward-compatible construction for any caller that doesn't set it.
    behavior_instructions: list[SeedObjectRef] = field(default_factory=list)
    roles_written: int = 0

    @property
    def behavior_instructions_written(self) -> int:
        return len(self.behavior_instructions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "profile": self.profile,
            "skills": [item.to_dict() for item in self.skills],
            "playbooks": [item.to_dict() for item in self.playbooks],
            "concepts": [item.to_dict() for item in self.concepts],
            "behavior_instructions": [item.to_dict() for item in self.behavior_instructions],
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
    `(workspace_id, name)`. It writes ONLY generic discipline objects:
    one skill, one playbook, vocabulary concepts, and (1.2.3+) every
    project-AGNOSTIC behavior_instruction registered in
    ``DISCIPLINE_FACTORIES``. It deliberately avoids project-specific
    roles, language preferences, communication style, or personality rules.
    """

    skill = upsert_agent_skill(conn, memory_population_skill(workspace_id, source_episode_id))
    playbook = upsert_agent_playbook(
        conn, memory_bootstrap_playbook(workspace_id, source_episode_id)
    )
    concepts = [
        upsert_domain_concept(conn, payload)
        for payload in vocabulary_concepts(workspace_id, source_episode_id)
    ]
    # Every generic-discipline factory in DISCIPLINE_FACTORIES is upserted.
    # Adding a new rule is one line in seed_behavior.py — no orchestrator
    # change. Order in the registry is preserved for deterministic output.
    discipline_bis = [
        upsert_behavior_instruction(conn, factory(workspace_id, source_episode_id))
        for factory in DISCIPLINE_FACTORIES
    ]
    # Subset listed in PINNED_DISCIPLINE_FACTORIES gets pinned after
    # upsert so it rides every active envelope regardless of query
    # relevance. The factory references give us deterministic name
    # resolution even though the upserted object's id is what we pin.
    _pinned_names = {factory.__name__ for factory in PINNED_DISCIPLINE_FACTORIES}
    for factory, bi in zip(DISCIPLINE_FACTORIES, discipline_bis, strict=True):
        if factory.__name__ in _pinned_names:
            pin_memory_object(
                conn,
                workspace_id=workspace_id,
                kind="behavior",
                object_id=bi.id,
                pinned=True,
            )

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
        behavior_instructions=[
            SeedObjectRef(kind="behavior_instruction", id=bi.id, name=bi.name)
            for bi in discipline_bis
        ],
    )
