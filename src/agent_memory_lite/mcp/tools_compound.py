"""Move 2 compound MCP handler.

Mirrors the HTTP route in api/routes/record_compound.py — composes
ingest_episode + write_decision + optional link_capability into one
call. The MCP layer takes the same payload shape as the HTTP request
schema and returns the same response shape.

The handler explicitly threads the just-created episode_id into the
decision (deterministic; doesn't rely on the Move 1 auto-thread
fallback) and reuses the existing writer modules so audit / trace /
candidate-extraction pipelines fire normally for each underlying step.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.ingestion._write_helpers import (
    capability_suggestion_dicts,
    decision_neighbor_dicts,
    record_supersede_feedback,
)
from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.capability_links import CapabilityLinkIn
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.enums import (
    CapabilityLinkRelation,
    CapabilityLinkTargetType,
    CapabilityType,
    EpisodeSource,
    TrustLevel,
)
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.vector_store.base import VectorStore


def memory_record_with_evidence(
    *,
    conn: sqlite3.Connection,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    payload: dict[str, Any],
    settings: Settings | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """MCP local-fallback compound write. Round-2 parity fix:
    * Threads ``settings`` (via the caller or lazy ``get_settings``)
      through to ``ingest_episode`` so ``MEMORY_AUTO_PROMOTE_*`` env
      flags fire on the MCP path. Previously the MCP fallback omitted
      ``auto_promote_settings``, silently disabling auto-promotion of
      decisions / rules from episode content for MCP-only deployments.
    * Calls ``record_supersede_feedback`` so the outcome loop sees a
      superseded decision the same way the HTTP route does.
    """
    workspace_id = str(payload.get("workspace_id") or "default")
    if settings is None:
        # Lazy import to avoid a top-level dep cycle for handlers that
        # don't need settings (e.g. unit tests calling this directly).
        from agent_memory_lite.config.settings import (  # noqa: PLC0415
            get_settings,
        )

        settings = get_settings()

    # Step 1: ingest the supporting episode.
    episode_in = EpisodeIn(
        workspace_id=workspace_id,
        session_id=payload.get("session_id"),
        task_id=payload.get("task_id"),
        source_type=EpisodeSource(payload.get("evidence_source_type", "agent_action")),
        raw_text=str(payload["evidence_text"]),
        trust_level=TrustLevel(payload.get("evidence_trust_level", "agent_observed")),
        importance=float(payload.get("evidence_importance", 0.6)),
    )
    ingest_result = ingest_episode(
        conn,
        episode_in,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        auto_promote_settings=settings,
    )
    episode_id = ingest_result.episode.id

    # Step 2: write the decision with the freshly-created episode id
    # threaded explicitly.
    decision = write_decision(
        conn,
        DecisionIn(
            workspace_id=workspace_id,
            title=str(payload["decision_title"]),
            decision_text=str(payload["decision_text"]),
            rationale=payload.get("decision_rationale"),
            supersedes_decision_id=payload.get("supersedes_decision_id"),
            source_episode_id=episode_id,
            confidence=float(payload.get("decision_confidence", 0.9)),
            importance=float(payload.get("decision_importance", 0.8)),
            references=list(payload.get("references", [])),
        ),
    )

    # Phase 1 outcome-loop parity: drop the superseded decision's
    # outcome_score the same way the HTTP route does. Failure-soft;
    # implicit feedback never blocks the parent write.
    record_supersede_feedback(
        conn,
        workspace_id=workspace_id,
        source_type="decision",
        superseded_id=decision.supersedes_decision_id,
        settings=settings,
    )

    # Step 3 (optional): link the decision to a capability.
    cap_type = payload.get("capability_type")
    cap_name = payload.get("capability_name")
    cap_relation = payload.get("capability_relation")
    link_id: str | None = None
    if cap_type and cap_name and cap_relation:
        link = link_capability(
            conn,
            CapabilityLinkIn(
                workspace_id=workspace_id,
                target_type=CapabilityLinkTargetType.DECISION,
                target_id=decision.id,
                capability_type=CapabilityType(cap_type),
                capability_name=str(cap_name),
                relation=CapabilityLinkRelation(cap_relation),
                rationale=payload.get("capability_rationale"),
                strength=float(payload.get("capability_strength", 0.7)),
                source_episode_id=episode_id,
            ),
        )
        link_id = link.id

    # Move 3 + Move 5: surface capability suggestions (when no link was
    # made) and decision neighbors (always) so MCP local-fallback callers
    # get the same hints they'd see via the HTTP route.
    suggestions = (
        capability_suggestion_dicts(
            conn,
            workspace_id=workspace_id,
            title=str(payload["decision_title"]),
            text=str(payload["decision_text"]),
            rationale=payload.get("decision_rationale"),
        )
        if link_id is None
        else []
    )
    neighbors = decision_neighbor_dicts(
        conn,
        workspace_id=workspace_id,
        title=str(payload["decision_title"]),
        text=str(payload["decision_text"]),
        rationale=payload.get("decision_rationale"),
        exclude_id=decision.id,
    )
    return {
        "workspace_id": workspace_id,
        "episode_id": episode_id,
        "decision_id": decision.id,
        "decision_status": decision.status.value,
        "valid_from": decision.valid_from,
        "superseded_decision_id": decision.supersedes_decision_id,
        "capability_link_id": link_id,
        "chunk_id": ingest_result.chunk.id,
        "capability_suggestions": suggestions,
        "decision_neighbors": neighbors,
    }
