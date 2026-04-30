"""Tool definitions exposed via MCP.

Each tool maps to the same service function used by the HTTP routes. The
JSON schema is intentionally loose; the underlying functions own validation.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.ingestion.behavior_writer import upsert_behavior_instruction
from agent_memory_lite.ingestion.candidate_writer import (
    promote_memory_candidate,
    reject_memory_candidate,
)
from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.ingestion.capability_writer import (
    upsert_agent_playbook,
    upsert_agent_role,
    upsert_agent_skill,
)
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.file_pipeline import ingest_file
from agent_memory_lite.ingestion.research_writer import (
    add_experiment_result,
    distill_insight,
    register_snapshot,
    update_insight,
    upsert_domain_concept,
    write_experiment,
)
from agent_memory_lite.ingestion.task_state_writer import write_task_state
from agent_memory_lite.ingestion.theory_writer import add_theory_evidence, write_theory
from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.models.capabilities import AgentPlaybookIn, AgentRoleIn, AgentSkillIn
from agent_memory_lite.models.capability_links import CapabilityLinkIn
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.research import (
    DomainConceptIn,
    ExperimentIn,
    ExperimentResultIn,
    MemorySnapshotIn,
    ResearchInsightIn,
    ResearchInsightUpdateIn,
)
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.models.task_state import TaskStateIn
from agent_memory_lite.models.theories import TheoryEvidenceIn, TheoryIn
from agent_memory_lite.repositories.behavior_repo import list_behavior_instructions
from agent_memory_lite.repositories.candidates_repo import list_candidates
from agent_memory_lite.repositories.capabilities_repo import build_agent_capabilities
from agent_memory_lite.repositories.capability_links_repo import list_capability_links
from agent_memory_lite.repositories.maintenance_repo import (
    list_maintenance_events,
    resolve_maintenance_event,
)
from agent_memory_lite.repositories.research_repo import (
    build_research_agenda,
    list_concepts,
    list_insights,
)
from agent_memory_lite.repositories.theories_repo import (
    list_evidence_for_theory,
    list_theories,
)
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.base import VectorStore

ToolHandler = Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    handler: ToolHandler


def _memory_get_context(
    *,
    conn: sqlite3.Connection,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    workspace_id: str = "default",
    query: str,
    task_id: str | None = None,
    max_tokens: int = 3500,
    historical: bool = False,
) -> dict[str, Any]:
    built = build_context(
        conn,
        RetrievalQuery(
            workspace_id=workspace_id,
            query=query,
            task_id=task_id,
            max_tokens=max_tokens,
            historical=historical,
        ),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    return {
        "context_text": built.text,
        "sources": [
            {"id": hit.id, "score": hit.score, "sources": hit.sources} for hit in built.hits
        ],
    }


def _memory_ingest_episode(
    *,
    conn: sqlite3.Connection,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    result = ingest_episode(
        conn,
        EpisodeIn(**payload),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    return {
        "episode_id": result.episode.id,
        "chunk_id": result.chunk.id,
        "redacted_text": result.episode.raw_text,
        "redacted_kinds": result.redacted_kinds,
        "candidates_written": result.candidates_written,
    }


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    return {
        "candidate_id": candidate.id,
        "workspace_id": candidate.workspace_id,
        "kind": candidate.kind.value,
        "subject": candidate.subject,
        "predicate": candidate.predicate,
        "object": candidate.object,
        "evidence": candidate.evidence,
        "confidence": candidate.confidence,
        "importance": candidate.importance,
        "trust_level": candidate.trust_level.value,
        "source_episode_id": candidate.source_episode_id,
        "status": candidate.status.value,
        "promoted_target_type": candidate.promoted_target_type,
        "promoted_target_id": candidate.promoted_target_id,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
        "decided_at": candidate.decided_at,
    }


def _capability_link_payload(link: Any) -> dict[str, Any]:
    return {
        "link_id": link.id,
        "workspace_id": link.workspace_id,
        "target_type": link.target_type.value,
        "target_id": link.target_id,
        "capability_type": link.capability_type.value,
        "capability_id": link.capability_id,
        "capability_name": link.capability_name,
        "relation": link.relation.value,
        "rationale": link.rationale,
        "strength": link.strength,
        "source_episode_id": link.source_episode_id,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
    }


def _maintenance_event_payload(event: Any) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "workspace_id": event.workspace_id,
        "kind": event.kind,
        "severity": event.severity.value,
        "status": event.status.value,
        "summary": event.summary,
        "details": event.details,
        "source_episode_id": event.source_episode_id,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "created_at": event.created_at,
        "resolved_at": event.resolved_at,
    }


def _behavior_instruction_payload(item: Any) -> dict[str, Any]:
    return {
        "instruction_id": item.id,
        "workspace_id": item.workspace_id,
        "name": item.name,
        "kind": item.kind.value,
        "scope": item.scope.value,
        "priority": item.priority.value,
        "rule": item.rule,
        "rationale": item.rationale,
        "applies_to": item.applies_to,
        "conflict_policy": item.conflict_policy.value,
        "source_episode_id": item.source_episode_id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "reviewed_by": item.reviewed_by,
        "reviewed_at": item.reviewed_at,
        "expires_at": item.expires_at,
        "last_applied_at": item.last_applied_at,
        "application_count": item.application_count,
        "conflict_group": item.conflict_group,
        "confidence": item.confidence,
        "active": item.active,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _memory_list_candidates(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    statuses: list[str] | None = None,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    from agent_memory_lite.models.enums import MemoryCandidateStatus  # noqa: PLC0415

    parsed = [MemoryCandidateStatus(item) for item in statuses] if statuses else None
    return {
        "candidates": [
            _candidate_payload(candidate)
            for candidate in list_candidates(
                conn,
                workspace_id=workspace_id,
                query=query,
                statuses=parsed,
                limit=limit,
            )
        ]
    }


def _memory_promote_candidate(
    *,
    conn: sqlite3.Connection,
    candidate_id: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    return _candidate_payload(promote_memory_candidate(conn, candidate_id=candidate_id))


def _memory_reject_candidate(
    *,
    conn: sqlite3.Connection,
    candidate_id: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    return _candidate_payload(reject_memory_candidate(conn, candidate_id=candidate_id))


def _memory_list_maintenance_events(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    statuses: list[str] | None = None,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    from agent_memory_lite.models.enums import MaintenanceEventStatus  # noqa: PLC0415

    parsed = [MaintenanceEventStatus(item) for item in statuses] if statuses else None
    return {
        "events": [
            _maintenance_event_payload(event)
            for event in list_maintenance_events(
                conn,
                workspace_id=workspace_id,
                statuses=parsed,
                limit=limit,
            )
        ]
    }


def _memory_resolve_maintenance_event(
    *,
    conn: sqlite3.Connection,
    event_id: str,
    status: str = "resolved",
    **_kwargs: Any,
) -> dict[str, Any]:
    from agent_memory_lite.models.enums import MaintenanceEventStatus  # noqa: PLC0415

    event = resolve_maintenance_event(
        conn,
        event_id=event_id,
        status=MaintenanceEventStatus(status),
        resolved_at=iso_now(),
    )
    if event is None:
        raise ValueError(f"maintenance event not found: {event_id}")
    return _maintenance_event_payload(event)


def _memory_link_capability(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    return _capability_link_payload(link_capability(conn, CapabilityLinkIn(**payload)))


def _memory_list_capability_links(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    target_type: str | None = None,
    target_id: str | None = None,
    capability_type: str | None = None,
    capability_id: str | None = None,
    limit: int = 50,
    **_kwargs: Any,
) -> dict[str, Any]:
    from agent_memory_lite.models.enums import (  # noqa: PLC0415
        CapabilityLinkTargetType,
        CapabilityType,
    )

    return {
        "links": [
            _capability_link_payload(link)
            for link in list_capability_links(
                conn,
                workspace_id=workspace_id,
                target_type=CapabilityLinkTargetType(target_type) if target_type else None,
                target_id=target_id,
                capability_type=CapabilityType(capability_type) if capability_type else None,
                capability_id=capability_id,
                limit=limit,
            )
        ]
    }


def _memory_search(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str,
    limit: int = 10,
    **_kwargs: Any,
) -> dict[str, Any]:
    hits = search_chunks_fts(
        conn,
        workspace_id=workspace_id,
        query=query,
        limit=limit,
    )
    return {
        "mode": "fts",
        "hits": [
            {
                "chunk_id": hit.chunk_id,
                "score": hit.score,
                "path": hit.path,
                "text": hit.text,
                "summary": hit.summary,
            }
            for hit in hits
        ],
    }


def _memory_ingest_file(
    *,
    conn: sqlite3.Connection,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    result = ingest_file(
        conn,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        **payload,
    )
    return {
        "file_id": result.file.id,
        "path": result.file.path,
        "chunks_written": result.chunks_written,
        "skipped": result.skipped,
    }


def _memory_write_decision(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    decision = write_decision(conn, DecisionIn(**payload))
    return {"decision_id": decision.id, "status": decision.status.value}


def _memory_update_task_state(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    state = write_task_state(conn, TaskStateIn(**payload))
    return {"state_id": state.id, "task_id": state.task_id, "status": state.status}


def _memory_write_theory(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    theory = write_theory(conn, TheoryIn(**payload))
    return {
        "theory_id": theory.id,
        "status": theory.status.value,
        "confidence": theory.confidence,
        "importance": theory.importance,
        "evidence_count": theory.evidence_count,
        "evidence_strength": theory.evidence_strength,
    }


def _memory_add_theory_evidence(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    evidence = add_theory_evidence(conn, TheoryEvidenceIn(**payload))
    return {
        "evidence_id": evidence.id,
        "theory_id": evidence.theory_id,
        "kind": evidence.kind.value,
        "observed_at": evidence.observed_at,
    }


def _memory_list_theories(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    limit: int = 20,
    include_archived: bool = False,
    include_evidence: bool = False,
    evidence_limit: int = 3,
    **_kwargs: Any,
) -> dict[str, Any]:
    theories = list_theories(
        conn,
        workspace_id=workspace_id,
        query=query,
        limit=limit,
        include_archived=include_archived,
    )
    return {
        "theories": [
            {
                "theory_id": theory.id,
                "title": theory.title,
                "domain": theory.domain,
                "claim": theory.claim,
                "validation_criteria": theory.validation_criteria,
                "dependent_decision_ids": theory.dependent_decision_ids,
                "status": theory.status.value,
                "confidence": theory.confidence,
                "importance": theory.importance,
                "evidence_count": theory.evidence_count,
                "evidence_strength": theory.evidence_strength,
                "tags": theory.tags,
                "evidence": [
                    {
                        "evidence_id": evidence.id,
                        "kind": evidence.kind.value,
                        "summary": evidence.summary,
                        "confidence": evidence.confidence,
                        "observed_at": evidence.observed_at,
                    }
                    for evidence in (
                        list_evidence_for_theory(conn, theory.id, limit=evidence_limit)
                        if include_evidence
                        else []
                    )
                ],
            }
            for theory in theories
        ],
    }


def _memory_register_snapshot(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    snapshot = register_snapshot(conn, MemorySnapshotIn(**payload))
    return {
        "snapshot_id": snapshot.id,
        "snapshot_key": snapshot.snapshot_key,
        "total_rows": snapshot.total_rows,
        "duckdb_path": snapshot.duckdb_path,
        "updated_at": snapshot.updated_at,
    }


def _memory_write_experiment(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    experiment = write_experiment(conn, ExperimentIn(**payload))
    return {
        "experiment_id": experiment.id,
        "theory_id": experiment.theory_id,
        "snapshot_id": experiment.snapshot_id,
        "status": experiment.status.value,
        "priority": experiment.priority,
    }


def _memory_add_experiment_result(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    result = add_experiment_result(conn, ExperimentResultIn(**payload))
    return {
        "result_id": result.id,
        "experiment_id": result.experiment_id,
        "theory_id": result.theory_id,
        "kind": result.kind.value,
        "observed_at": result.observed_at,
    }


def _memory_upsert_concept(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    concept = upsert_domain_concept(conn, DomainConceptIn(**payload))
    return {
        "concept_id": concept.id,
        "name": concept.name,
        "kind": concept.kind.value,
        "confidence": concept.confidence,
        "active": concept.active,
    }


def _memory_distill_insight(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    insight = distill_insight(conn, ResearchInsightIn(**payload))
    return {
        "insight_id": insight.id,
        "insight_type": insight.insight_type.value,
        "status": insight.status.value,
        "target_type": insight.target_type,
        "target_id": insight.target_id,
    }


def _memory_update_insight(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    insight = update_insight(conn, ResearchInsightUpdateIn(**payload))
    return {
        "insight_id": insight.id,
        "insight_type": insight.insight_type.value,
        "status": insight.status.value,
        "target_type": insight.target_type,
        "target_id": insight.target_id,
        "updated_at": insight.updated_at,
    }


def _memory_list_research_agenda(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    limit: int = 10,
    **_kwargs: Any,
) -> dict[str, Any]:
    agenda = build_research_agenda(
        conn,
        workspace_id=workspace_id,
        query=query,
        limit=limit,
    )
    return {
        "snapshots": [
            {
                "snapshot_id": item.id,
                "snapshot_key": item.snapshot_key,
                "title": item.title,
                "total_rows": item.total_rows,
                "duckdb_path": item.duckdb_path,
            }
            for item in agenda.snapshots
        ],
        "experiments": [
            {
                "experiment_id": item.id,
                "title": item.title,
                "theory_id": item.theory_id,
                "snapshot_id": item.snapshot_id,
                "status": item.status.value,
                "priority": item.priority,
                "hypothesis": item.hypothesis,
            }
            for item in agenda.experiments
        ],
        "insights": [
            {
                "insight_id": item.id,
                "insight_type": item.insight_type.value,
                "summary": item.summary,
                "status": item.status.value,
                "confidence": item.confidence,
                "target_type": item.target_type,
                "target_id": item.target_id,
            }
            for item in agenda.insights
        ],
        "concepts": [
            {
                "concept_id": item.id,
                "name": item.name,
                "kind": item.kind.value,
                "definition": item.definition,
                "confidence": item.confidence,
            }
            for item in agenda.concepts
        ],
    }


def _memory_list_concepts(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    concepts = list_concepts(
        conn,
        workspace_id=workspace_id,
        query=query,
        include_inactive=include_inactive,
        limit=limit,
    )
    return {
        "concepts": [
            {
                "concept_id": item.id,
                "name": item.name,
                "kind": item.kind.value,
                "definition": item.definition,
                "confidence": item.confidence,
                "active": item.active,
            }
            for item in concepts
        ],
    }


def _memory_list_insights(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    insights = list_insights(
        conn,
        workspace_id=workspace_id,
        query=query,
        limit=limit,
    )
    return {
        "insights": [
            {
                "insight_id": item.id,
                "insight_type": item.insight_type.value,
                "summary": item.summary,
                "status": item.status.value,
                "confidence": item.confidence,
                "target_type": item.target_type,
                "target_id": item.target_id,
            }
            for item in insights
        ],
    }


def _memory_upsert_agent_role(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    role = upsert_agent_role(conn, AgentRoleIn(**payload))
    return {
        "role_id": role.id,
        "name": role.name,
        "confidence": role.confidence,
        "active": role.active,
    }


def _memory_upsert_agent_skill(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    skill = upsert_agent_skill(conn, AgentSkillIn(**payload))
    return {
        "skill_id": skill.id,
        "name": skill.name,
        "confidence": skill.confidence,
        "active": skill.active,
    }


def _memory_upsert_agent_playbook(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    playbook = upsert_agent_playbook(conn, AgentPlaybookIn(**payload))
    return {
        "playbook_id": playbook.id,
        "name": playbook.name,
        "confidence": playbook.confidence,
        "active": playbook.active,
    }


def _memory_list_agent_capabilities(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 6,
    **_kwargs: Any,
) -> dict[str, Any]:
    capabilities = build_agent_capabilities(
        conn,
        workspace_id=workspace_id,
        query=query,
        include_inactive=include_inactive,
        limit=limit,
    )
    return {
        "roles": [
            {
                "role_id": item.id,
                "name": item.name,
                "purpose": item.purpose,
                "confidence": item.confidence,
                "active": item.active,
            }
            for item in capabilities.roles
        ],
        "skills": [
            {
                "skill_id": item.id,
                "name": item.name,
                "summary": item.summary,
                "confidence": item.confidence,
                "active": item.active,
            }
            for item in capabilities.skills
        ],
        "playbooks": [
            {
                "playbook_id": item.id,
                "name": item.name,
                "goal": item.goal,
                "confidence": item.confidence,
                "active": item.active,
            }
            for item in capabilities.playbooks
        ],
    }


def _memory_upsert_behavior_instruction(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    instruction = upsert_behavior_instruction(conn, BehaviorInstructionIn(**payload))
    return _behavior_instruction_payload(instruction)


def _memory_list_behavior_instructions(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    kinds: list[str] | None = None,
    include_inactive: bool = False,
    limit: int = 10,
    **_kwargs: Any,
) -> dict[str, Any]:
    from agent_memory_lite.models.enums import BehaviorInstructionKind  # noqa: PLC0415

    parsed_kinds = [BehaviorInstructionKind(kind) for kind in kinds] if kinds else None
    return {
        "instructions": [
            _behavior_instruction_payload(item)
            for item in list_behavior_instructions(
                conn,
                workspace_id=workspace_id,
                query=query,
                kinds=parsed_kinds,
                include_inactive=include_inactive,
                limit=limit,
            )
        ]
    }


TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="memory_get_context",
        description="Retrieve memory context for the agent before a task.",
        handler=_memory_get_context,
    ),
    ToolDefinition(
        name="memory_ingest_episode",
        description="Persist an event into episodic memory with redaction.",
        handler=_memory_ingest_episode,
    ),
    ToolDefinition(
        name="memory_search",
        description="Exact FTS lookup over chunks (BM25 ordered).",
        handler=_memory_search,
    ),
    ToolDefinition(
        name="memory_ingest_file",
        description="Index a single file into memory, idempotent by content hash.",
        handler=_memory_ingest_file,
    ),
    ToolDefinition(
        name="memory_list_candidates",
        description="List reviewable memory candidates created by extraction.",
        handler=_memory_list_candidates,
    ),
    ToolDefinition(
        name="memory_promote_candidate",
        description="Promote a reviewed memory candidate into its explicit target table.",
        handler=_memory_promote_candidate,
    ),
    ToolDefinition(
        name="memory_reject_candidate",
        description="Reject a memory candidate while preserving it as negative evidence.",
        handler=_memory_reject_candidate,
    ),
    ToolDefinition(
        name="memory_list_maintenance_events",
        description="List open/resolved maintenance events that affect memory integrity.",
        handler=_memory_list_maintenance_events,
    ),
    ToolDefinition(
        name="memory_resolve_maintenance_event",
        description="Mark a maintenance event resolved or ignored after review.",
        handler=_memory_resolve_maintenance_event,
    ),
    ToolDefinition(
        name="memory_link_capability",
        description="Link a role, skill, or playbook to a theory, experiment, evidence item, insight, candidate, or decision.",
        handler=_memory_link_capability,
    ),
    ToolDefinition(
        name="memory_list_capability_links",
        description="List capability links that explain which roles/skills/playbooks influence research memory objects.",
        handler=_memory_list_capability_links,
    ),
    ToolDefinition(
        name="memory_upsert_behavior_instruction",
        description="Create or update a persistent behavior instruction with explicit scope, priority, and conflict policy.",
        handler=_memory_upsert_behavior_instruction,
    ),
    ToolDefinition(
        name="memory_list_behavior_instructions",
        description="List persistent behavior instructions that should shape agent communication and operating behavior.",
        handler=_memory_list_behavior_instructions,
    ),
    ToolDefinition(
        name="memory_write_decision",
        description="Record an architectural decision; supports supersedes chains.",
        handler=_memory_write_decision,
    ),
    ToolDefinition(
        name="memory_update_task_state",
        description="Upsert task state for (workspace_id, task_id).",
        handler=_memory_update_task_state,
    ),
    ToolDefinition(
        name="memory_write_theory",
        description="Record a working research theory or hypothesis with claim, mechanism, predictions, and experiment plan.",
        handler=_memory_write_theory,
    ),
    ToolDefinition(
        name="memory_add_theory_evidence",
        description="Attach supporting, refuting, mixed, neutral, or experiment evidence to a theory.",
        handler=_memory_add_theory_evidence,
    ),
    ToolDefinition(
        name="memory_list_theories",
        description="List relevant theory/hypothesis memory items, optionally including recent evidence.",
        handler=_memory_list_theories,
    ),
    ToolDefinition(
        name="memory_register_snapshot",
        description="Register or update a research data snapshot with paths, build metadata, and table counts.",
        handler=_memory_register_snapshot,
    ),
    ToolDefinition(
        name="memory_write_experiment",
        description="Create a planned/running research experiment linked to a theory and/or data snapshot.",
        handler=_memory_write_experiment,
    ),
    ToolDefinition(
        name="memory_add_experiment_result",
        description="Record an experiment result; linked theory confidence/status is updated automatically.",
        handler=_memory_add_experiment_result,
    ),
    ToolDefinition(
        name="memory_upsert_concept",
        description="Create or update a domain concept so research vocabulary is explicit and reusable.",
        handler=_memory_upsert_concept,
    ),
    ToolDefinition(
        name="memory_distill_insight",
        description="Promote raw episode learnings into actionable insights or open questions.",
        handler=_memory_distill_insight,
    ),
    ToolDefinition(
        name="memory_update_insight",
        description="Update an existing research insight's target link or status.",
        handler=_memory_update_insight,
    ),
    ToolDefinition(
        name="memory_list_research_agenda",
        description="List current snapshots, open experiments, insights, and concepts relevant to a research query.",
        handler=_memory_list_research_agenda,
    ),
    ToolDefinition(
        name="memory_list_concepts",
        description="List domain concepts in the project memory.",
        handler=_memory_list_concepts,
    ),
    ToolDefinition(
        name="memory_list_insights",
        description="List distilled research insights and open questions.",
        handler=_memory_list_insights,
    ),
    ToolDefinition(
        name="memory_upsert_agent_role",
        description="Create or update a first-class agent role with responsibilities and boundaries.",
        handler=_memory_upsert_agent_role,
    ),
    ToolDefinition(
        name="memory_upsert_agent_skill",
        description="Create or update a reusable agent skill with inputs, outputs, and related roles.",
        handler=_memory_upsert_agent_skill,
    ),
    ToolDefinition(
        name="memory_upsert_agent_playbook",
        description="Create or update a repeatable agent playbook with triggers, steps, and success criteria.",
        handler=_memory_upsert_agent_playbook,
    ),
    ToolDefinition(
        name="memory_list_agent_capabilities",
        description="List relevant roles, skills, and playbooks for a query.",
        handler=_memory_list_agent_capabilities,
    ),
)


def dispatch(name: str, **kwargs: Any) -> dict[str, Any]:
    for tool in TOOLS:
        if tool.name == name:
            return tool.handler(**kwargs)
    raise KeyError(f"unknown MCP tool: {name!r}")
