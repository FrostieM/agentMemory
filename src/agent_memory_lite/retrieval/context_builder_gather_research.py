"""Research-agenda gather helper.

The research-agenda gather is the heaviest in the build pipeline (it
also pulls capability links per experiment + insight). Pulled out of
``context_builder_gather.py`` so that module can stay under the SLOC
ceiling.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.capability_links import CapabilityLink
from agent_memory_lite.models.enums import CapabilityLinkTargetType
from agent_memory_lite.models.research import Experiment, MemorySnapshot, ResearchAgenda
from agent_memory_lite.models.research_taxonomy import DomainConcept, ResearchInsight
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.repositories.capability_links_repo import list_capability_links_for_targets
from agent_memory_lite.repositories.research_repo import build_research_agenda
from agent_memory_lite.retrieval.context_builder_constants import (
    INDEX_FETCH_LIMIT,
    MAX_RESEARCH_AGENDA,
)


def _gather_research_agenda(
    conn: sqlite3.Connection, query: RetrievalQuery
) -> tuple[
    ResearchAgenda,
    list[MemorySnapshot],
    list[Experiment],
    list[ResearchInsight],
    list[DomainConcept],
    dict[str, list[CapabilityLink]],
    dict[str, list[CapabilityLink]],
]:
    full = build_research_agenda(
        conn, workspace_id=query.workspace_id, query=query.query, limit=INDEX_FETCH_LIMIT
    )
    agenda = ResearchAgenda(
        snapshots=list(full.snapshots[:MAX_RESEARCH_AGENDA]),
        experiments=list(full.experiments[:MAX_RESEARCH_AGENDA]),
        insights=list(full.insights[:MAX_RESEARCH_AGENDA]),
        concepts=list(full.concepts[:MAX_RESEARCH_AGENDA]),
    )
    experiment_links = list_capability_links_for_targets(
        conn,
        workspace_id=query.workspace_id,
        target_type=CapabilityLinkTargetType.EXPERIMENT,
        target_ids=[exp.id for exp in agenda.experiments],
        limit_per_target=3,
    )
    insight_links = list_capability_links_for_targets(
        conn,
        workspace_id=query.workspace_id,
        target_type=CapabilityLinkTargetType.RESEARCH_INSIGHT,
        target_ids=[ins.id for ins in agenda.insights],
        limit_per_target=3,
    )
    return (
        agenda,
        list(full.snapshots[MAX_RESEARCH_AGENDA:]),
        list(full.experiments[MAX_RESEARCH_AGENDA:]),
        list(full.insights[MAX_RESEARCH_AGENDA:]),
        list(full.concepts[MAX_RESEARCH_AGENDA:]),
        experiment_links,
        insight_links,
    )
