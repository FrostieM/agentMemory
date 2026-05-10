"""POST /memory/record_with_evidence — Move 2 of v2.2 consolidation.

Bundles the three discipline-rule follow-ups (ingest_episode →
write_decision → link_capability) into one atomic call. The agent
calls this single tool instead of remembering all three steps.

* The decision's source_episode_id is set to the just-written episode
  id explicitly — no reliance on the Move 1 auto-thread heuristic, the
  link is deterministic and self-contained.
* The capability link is optional; passing the (type, name, relation)
  triplet creates one, leaving them None creates a decision without a
  link. The schema validator enforces all-or-nothing.
* Each underlying call still flows through its own writer module so
  the existing audit_log / trace / candidate-extraction pipelines all
  fire normally — the only thing this route adds is composition.

Read-only: no mutation outside the three writes documented above.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    EmbeddingProviderDep,
    SettingsDep,
    VectorStoreDep,
    ensure_workspace_writable,
)
from agent_memory_lite.api.routes._capability_suggest_payload import (
    capability_suggestion_payloads,
)
from agent_memory_lite.api.schemas.record_compound import (
    RecordWithEvidenceRequest,
    RecordWithEvidenceResponse,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.capability_links import CapabilityLinkIn
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.enums import CapabilityLinkTargetType
from agent_memory_lite.models.episodes import EpisodeIn

router = APIRouter()


@router.post("/memory/record_with_evidence", response_model=RecordWithEvidenceResponse)
def record_with_evidence_route(
    body: RecordWithEvidenceRequest,
    conn: DbDep,
    provider: EmbeddingProviderDep,
    store: VectorStoreDep,
    settings: SettingsDep,
) -> RecordWithEvidenceResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/record_with_evidence",
        operation="record_with_evidence",
        label="Record decision with evidence",
        snippet=body.decision_title,
    ) as trace:
        # Step 1: ingest the supporting episode.
        episode_in = EpisodeIn(
            workspace_id=body.workspace_id,
            session_id=body.session_id,
            task_id=body.task_id,
            source_type=body.evidence_source_type,
            raw_text=body.evidence_text,
            trust_level=body.evidence_trust_level,
            importance=body.evidence_importance,
        )
        trace.stage_started("episode", "Ingest evidence episode")
        ingest_result = ingest_episode(
            conn,
            episode_in,
            embedding_provider=provider,
            vector_store=store,
            auto_promote_settings=settings,
        )
        episode_id = ingest_result.episode.id
        trace.stage_done(
            "episode",
            "Evidence episode ingested",
            counts={"episode_id": episode_id, "chunk_id": ingest_result.chunk.id},
        )

        # Step 2: write the decision with the freshly-created episode id
        # threaded explicitly. The Move 1 auto-thread fallback is bypassed
        # because we already know the answer — making the link visible in
        # code rather than implicit.
        trace.stage_started("decision", "Persist decision")
        decision = write_decision(
            conn,
            DecisionIn(
                workspace_id=body.workspace_id,
                title=body.decision_title,
                decision_text=body.decision_text,
                rationale=body.decision_rationale,
                supersedes_decision_id=body.supersedes_decision_id,
                source_episode_id=episode_id,
                confidence=body.decision_confidence,
                importance=body.decision_importance,
                references=body.references,
            ),
        )
        trace.stage_done(
            "decision",
            "Decision persisted",
            counts={
                "decision_id": decision.id,
                "supersedes": bool(decision.supersedes_decision_id),
            },
        )

        # Step 3 (optional): link the decision to a capability. Triplet
        # validity is enforced at schema-level; here we just gate on
        # presence.
        link_id: str | None = None
        if (
            body.capability_type is not None
            and body.capability_name is not None
            and body.capability_relation is not None
        ):
            trace.stage_started("capability_link", "Link decision to capability")
            link = link_capability(
                conn,
                CapabilityLinkIn(
                    workspace_id=body.workspace_id,
                    target_type=CapabilityLinkTargetType.DECISION,
                    target_id=decision.id,
                    capability_type=body.capability_type,
                    capability_name=body.capability_name,
                    relation=body.capability_relation,
                    rationale=body.capability_rationale,
                    strength=body.capability_strength,
                    source_episode_id=episode_id,
                ),
            )
            link_id = link.id
            trace.stage_done(
                "capability_link",
                "Capability link persisted",
                counts={"link_id": link_id, "relation": link.relation},
            )

        trace.graph_delta(
            object_type="decision",
            object_id=decision.id,
            action="created",
            label="Decision recorded with evidence",
            counts={"episode_id": episode_id, "linked": link_id is not None},
        )
        # Move 3: when no capability was passed, surface server-ranked
        # suggestions in the response so the agent (or operator via
        # /ui/review) sees the top 3 candidates and can decide whether
        # to accept any. Empty when a link was already created.
        suggestions = (
            capability_suggestion_payloads(
                conn,
                workspace_id=body.workspace_id,
                title=body.decision_title,
                text=body.decision_text,
                rationale=body.decision_rationale,
            )
            if link_id is None
            else []
        )
        response = RecordWithEvidenceResponse(
            workspace_id=body.workspace_id,
            episode_id=episode_id,
            decision_id=decision.id,
            decision_status=decision.status.value,
            valid_from=decision.valid_from,
            superseded_decision_id=decision.supersedes_decision_id,
            capability_link_id=link_id,
            chunk_id=ingest_result.chunk.id,
            capability_suggestions=[s.model_dump() for s in suggestions],
        )
        trace.stage_done(
            "response",
            "Compound response ready",
            counts={"decision_id": decision.id, "linked": link_id is not None},
        )
        return response
