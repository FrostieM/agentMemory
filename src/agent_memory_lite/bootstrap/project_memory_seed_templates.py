"""Template payloads for the neutral project-memory seed.

Split out of ``project_memory_seed.py`` so the orchestrator stays
under the SLOC ceiling. The factory functions keep ``workspace_id`` /
``source_episode_id`` parameters explicit instead of capturing them
from a closure, which makes them easy to call from tests.

Vocabulary concepts live in ``project_memory_concepts.py`` and are
re-exported here so existing imports keep working.
"""

from __future__ import annotations

from agent_memory_lite.bootstrap.project_memory_concepts import vocabulary_concepts
from agent_memory_lite.bootstrap.project_memory_seed_behavior import (
    DISCIPLINE_FACTORIES,
    capability_suggestion_discipline_instruction,
    search_before_write_discipline_instruction,
)
from agent_memory_lite.models.capabilities import AgentPlaybookIn, AgentSkillIn

__all__ = [
    "DISCIPLINE_FACTORIES",
    "capability_suggestion_discipline_instruction",
    "memory_bootstrap_playbook",
    "memory_population_skill",
    "search_before_write_discipline_instruction",
    "vocabulary_concepts",
]


def memory_population_skill(workspace_id: str, source_episode_id: str | None) -> AgentSkillIn:
    return AgentSkillIn(
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
            "memory_brief and memory_search before work",
            "memory_write(kind=plan_step) or memory_edit(kind=plan_step) as progress changes",
            "memory_write(kind=episode) after meaningful actions",
            "memory_write(kind=decision) only for committed choices",
            "memory_write(kind=theory) for research claims",
            "memory_write(kind=insight) for reusable findings",
            "candidate promote or reject decisions after extraction",
        ],
        tools=[
            "memory_brief",
            "memory_search",
            "memory_impact_check",
            "memory_write",
            "memory_edit",
            "memory_get",
            "memory_pin",
            "memory_archive",
            "scripts/memory_audit.py",
            "scripts/memory_hygiene.py",
            "scripts/memory_mcp_smoke.py",
        ],
        related_roles=[],
        source_episode_id=source_episode_id,
        confidence=0.92,
        active=True,
    )


def memory_bootstrap_playbook(workspace_id: str, source_episode_id: str | None) -> AgentPlaybookIn:
    return AgentPlaybookIn(
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
            "Call memory_brief before non-trivial work",
            "Call memory_search before editing specific files",
            "Call memory_impact_check before reading or editing source files",
            "Update plan_steps when the goal, plan, status, or next action changes",
            "Write episodes with memory_write(kind=episode) after meaningful actions and include verification evidence",
            "Use decisions only for committed architecture or operating choices",
            "Use theories for research claims and insights for durable reusable findings",
            "Review extraction candidates and promote or reject them explicitly",
            "Run audit, hygiene, and MCP smoke checks after setup, migration, or restart",
        ],
        success_criteria=[
            "Seed wrote only generic discipline rules (e.g. capability linkage); no language preference, communication style, personality, or project-specific role was seeded",
            "No default workspace rows are created in project mode",
            "Important work maps to first-class memory objects instead of raw episodes only",
            "Audit, hygiene, and MCP smoke checks can prove the memory surface is usable",
        ],
        required_skills=["Memory population discipline"],
        source_episode_id=source_episode_id,
        confidence=0.92,
        active=True,
    )
