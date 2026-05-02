"""Per-section gather helpers used by the ``build_context`` orchestrator.

Each helper hits the relevant repository, slices the result into a
top-N "full render" list and a long-tail "index" list, and returns both
so the renderer can show top items in full and the rest as compact
``<ref/>`` entries.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.behavior import BehaviorInstruction, BehaviorInstructionSet
from agent_memory_lite.models.capabilities import (
    AgentCapabilities,
    AgentPlaybook,
    AgentRole,
    AgentSkill,
)
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.enums import CapabilityLinkTargetType
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.models.theories import Theory
from agent_memory_lite.repositories.behavior_repo import build_behavior_instruction_set
from agent_memory_lite.repositories.capabilities_repo import build_agent_capabilities
from agent_memory_lite.repositories.capability_links_repo import list_capability_links_for_targets
from agent_memory_lite.repositories.decisions_repo import (
    list_active_decisions,
    list_all_decisions,
)
from agent_memory_lite.repositories.theories_repo import (
    list_evidence_for_theory,
    list_theories,
)
from agent_memory_lite.retrieval.context_builder_constants import (
    INDEX_FETCH_LIMIT,
    MAX_AGENT_CAPABILITIES,
    MAX_BEHAVIOR_INSTRUCTIONS,
    MAX_DECISIONS,
    MAX_HISTORICAL_DECISIONS,
    MAX_THEORIES,
    MAX_THEORY_EVIDENCE,
)
from agent_memory_lite.retrieval.context_builder_gather_research import _gather_research_agenda
from agent_memory_lite.retrieval.context_builder_models import TheoryContext

__all__ = [
    "_build_theory_contexts",
    "_gather_behavior",
    "_gather_capabilities",
    "_gather_decisions",
    "_gather_research_agenda",
    "_gather_theories",
]


def _gather_decisions(
    conn: sqlite3.Connection, query: RetrievalQuery
) -> tuple[list[Decision], list[Decision]]:
    if query.historical:
        all_decisions = list_all_decisions(
            conn, query.workspace_id, query=query.query, limit=INDEX_FETCH_LIMIT
        )
        return all_decisions[:MAX_HISTORICAL_DECISIONS], all_decisions[MAX_HISTORICAL_DECISIONS:]
    all_decisions = list_active_decisions(
        conn, query.workspace_id, query=query.query, limit=INDEX_FETCH_LIMIT
    )
    return all_decisions[:MAX_DECISIONS], all_decisions[MAX_DECISIONS:]


def _gather_theories(
    conn: sqlite3.Connection, query: RetrievalQuery
) -> tuple[list[Theory], list[Theory]]:
    all_theories = list_theories(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        limit=INDEX_FETCH_LIMIT,
        include_archived=query.historical,
    )
    return all_theories[:MAX_THEORIES], all_theories[MAX_THEORIES:]


def _build_theory_contexts(
    conn: sqlite3.Connection, *, workspace_id: str, theories: list[Theory]
) -> list[TheoryContext]:
    links = list_capability_links_for_targets(
        conn,
        workspace_id=workspace_id,
        target_type=CapabilityLinkTargetType.THEORY,
        target_ids=[theory.id for theory in theories],
        limit_per_target=3,
    )
    return [
        TheoryContext(
            theory=theory,
            evidence=list_evidence_for_theory(conn, theory.id, limit=MAX_THEORY_EVIDENCE),
            capability_links=links.get(theory.id, []),
        )
        for theory in theories
    ]


def _gather_behavior(
    conn: sqlite3.Connection, query: RetrievalQuery
) -> tuple[BehaviorInstructionSet | None, list[BehaviorInstruction]]:
    full = build_behavior_instruction_set(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        include_inactive=query.historical,
        limit=INDEX_FETCH_LIMIT,
    )
    if not full:
        return None, []
    return (
        BehaviorInstructionSet(instructions=list(full.instructions[:MAX_BEHAVIOR_INSTRUCTIONS])),
        list(full.instructions[MAX_BEHAVIOR_INSTRUCTIONS:]),
    )


def _gather_capabilities(
    conn: sqlite3.Connection, query: RetrievalQuery
) -> tuple[
    AgentCapabilities | None,
    list[AgentRole],
    list[AgentSkill],
    list[AgentPlaybook],
]:
    full = build_agent_capabilities(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        include_inactive=query.historical,
        limit=INDEX_FETCH_LIMIT,
    )
    if full is None:
        return None, [], [], []
    return (
        AgentCapabilities(
            roles=list(full.roles[:MAX_AGENT_CAPABILITIES]),
            skills=list(full.skills[:MAX_AGENT_CAPABILITIES]),
            playbooks=list(full.playbooks[:MAX_AGENT_CAPABILITIES]),
        ),
        list(full.roles[MAX_AGENT_CAPABILITIES:]),
        list(full.skills[MAX_AGENT_CAPABILITIES:]),
        list(full.playbooks[MAX_AGENT_CAPABILITIES:]),
    )
