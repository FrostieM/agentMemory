"""Neutral seed data for newly created project memory databases.

This seed is intentionally narrow: it teaches the memory database how to be
populated, not how an agent should speak, code, or make project decisions.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from agent_memory_lite.ingestion.capability_writer import (
    upsert_agent_playbook,
    upsert_agent_skill,
)
from agent_memory_lite.ingestion.research_writer import upsert_domain_concept
from agent_memory_lite.models.capabilities import AgentPlaybookIn, AgentSkillIn
from agent_memory_lite.models.enums import ConceptKind
from agent_memory_lite.models.research import DomainConceptIn


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

    skill = upsert_agent_skill(
        conn,
        AgentSkillIn(
            workspace_id=workspace_id,
            name="Memory population discipline",
            summary=(
                "Use the correct first-class memory object for important work: episodes "
                "for audit trail, task_state for progress, decisions for committed "
                "choices, theories/evidence/experiments/results for research, snapshots "
                "for datasets, candidates for review queues, and capabilities for reusable "
                "memory workflows."
            ),
            when_to_use=[
                "A non-trivial task starts or finishes",
                "A file or architecture decision is about to change",
                "A database export, replay, or research dataset is used",
                "A hypothesis, experiment, result, or reusable lesson appears",
                "Extraction candidates need promote or reject review",
            ],
            inputs=[
                "Task goal and current plan",
                "Files or artifacts touched",
                "Verification command output",
                "Relevant object ids and artifact paths",
            ],
            outputs=[
                "memory_get_context and memory_search before work",
                "memory_update_task_state as progress changes",
                "memory_ingest_episode after meaningful actions",
                "memory_write_decision only for committed choices",
                "memory_write_theory, evidence, experiments, and results for research",
                "memory_register_snapshot before data/replay analysis",
                "candidate promote or reject decisions after extraction",
            ],
            tools=[
                "memory_get_context",
                "memory_search",
                "memory_update_task_state",
                "memory_ingest_episode",
                "memory_write_decision",
                "memory_write_theory",
                "memory_add_theory_evidence",
                "memory_write_experiment",
                "memory_add_experiment_result",
                "memory_register_snapshot",
                "memory_list_candidates",
                "memory_promote_candidate",
                "memory_reject_candidate",
                "scripts/memory_audit.py",
                "scripts/memory_hygiene.py",
                "scripts/memory_mcp_smoke.py",
            ],
            related_roles=[],
            source_episode_id=source_episode_id,
            confidence=0.92,
            active=True,
        ),
    )

    playbook = upsert_agent_playbook(
        conn,
        AgentPlaybookIn(
            workspace_id=workspace_id,
            name="Neutral memory bootstrap",
            goal=(
                "Keep a new local memory database useful by recording durable memory "
                "objects without changing agent communication style, language, personality, "
                "or project-specific behavior."
            ),
            triggers=[
                "A project memory database is created",
                "A new agent session starts in a project",
                "A non-trivial task completes",
            ],
            steps=[
                "Call memory_get_context before non-trivial work",
                "Call memory_search before editing specific files",
                "Update task_state when the goal, plan, status, or next action changes",
                "Write episodes after meaningful actions and include verification evidence",
                "Use decisions only for committed architecture or operating choices",
                "Use theories, evidence, experiments, and results for research claims",
                "Register snapshots before database exports, replays, or data analysis",
                "Review extraction candidates and promote or reject them explicitly",
                "Run audit, hygiene, and MCP smoke checks after setup, migration, or restart",
            ],
            success_criteria=[
                "No behavior instruction, language preference, communication style, or personality rule was seeded",
                "No project-specific role or preference was seeded",
                "No default workspace rows are created in project mode",
                "Important work maps to first-class memory objects instead of raw episodes only",
                "Audit, hygiene, and MCP smoke checks can prove the memory surface is usable",
            ],
            required_skills=["Memory population discipline"],
            source_episode_id=source_episode_id,
            confidence=0.92,
            active=True,
        ),
    )

    concepts = [
        upsert_domain_concept(
            conn,
            DomainConceptIn(
                workspace_id=workspace_id,
                name="workspace_id",
                kind=ConceptKind.TERM,
                definition=(
                    "Logical namespace inside a memory database. In project mode, use the "
                    "project's established workspace id and do not silently write durable "
                    "rows to default."
                ),
                aliases=["memory namespace", "workspace namespace"],
                tags=["memory-bootstrap", "workspace-isolation"],
                source_episode_id=source_episode_id,
                confidence=0.9,
            ),
        ),
        upsert_domain_concept(
            conn,
            DomainConceptIn(
                workspace_id=workspace_id,
                name="memory candidate review",
                kind=ConceptKind.TERM,
                definition=(
                    "Review-first workflow where extracted candidates are promoted only when "
                    "evidence supports them and rejected when weak or wrong."
                ),
                aliases=["candidate triage", "candidate promote reject"],
                tags=["memory-bootstrap", "review-queue"],
                source_episode_id=source_episode_id,
                confidence=0.9,
            ),
        ),
        upsert_domain_concept(
            conn,
            DomainConceptIn(
                workspace_id=workspace_id,
                name="memory snapshot",
                kind=ConceptKind.ARTIFACT,
                definition=(
                    "Immutable reference to a database export, replay dataset, or research "
                    "artifact with paths and table counts so later experiments are repeatable."
                ),
                aliases=["dataset snapshot", "research snapshot"],
                tags=["memory-bootstrap", "research"],
                source_episode_id=source_episode_id,
                confidence=0.9,
            ),
        ),
        upsert_domain_concept(
            conn,
            DomainConceptIn(
                workspace_id=workspace_id,
                name="memory integrity audit",
                kind=ConceptKind.TERM,
                definition=(
                    "Read-only proof that SQLite, FTS, vector rows, workspace isolation, "
                    "hygiene, and MCP retrieval are consistent enough to trust."
                ),
                aliases=["memory audit", "retrieval integrity"],
                tags=["memory-bootstrap", "maintenance"],
                source_episode_id=source_episode_id,
                confidence=0.9,
            ),
        ),
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
