"""POST /memory/ingest_episode — write an episode + chunk + FTS row + vector."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    EmbeddingProviderDep,
    SettingsDep,
    VectorStoreDep,
    ensure_workspace_writable,
)
from agent_memory_lite.api.schemas.ingest import (
    IngestEpisodeRequest,
    IngestEpisodeResponse,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.api.workspace_routing import ensure_workspace_matches_db
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.models.episodes import EpisodeIn

router = APIRouter()


@router.post("/memory/ingest_episode", response_model=IngestEpisodeResponse)
def ingest_episode_route(
    body: IngestEpisodeRequest,
    conn: DbDep,
    provider: EmbeddingProviderDep,
    store: VectorStoreDep,
    settings: SettingsDep,
) -> IngestEpisodeResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    ensure_workspace_matches_db(conn, body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/ingest_episode",
        operation="ingest",
        label="Ingest episode",
        snippet=body.raw_text,
    ) as trace:
        trace.stage_started("redact", "Redact incoming episode", snippet=body.raw_text)
        episode_in = EpisodeIn(
            workspace_id=body.workspace_id,
            session_id=body.session_id,
            task_id=body.task_id,
            source_type=body.source_type,
            raw_text=body.raw_text,
            summary=body.summary,
            label=body.label,
            trust_level=body.trust_level,
            importance=body.importance,
            confidence=body.confidence,
            metadata=body.metadata,
        )
        trace.stage_done("redact", "Episode text redacted", counts={"chars": len(body.raw_text)})
        trace.stage_started("persist", "Persist episode and chunk")
        result = ingest_episode(
            conn,
            episode_in,
            embedding_provider=provider,
            vector_store=store,
            auto_promote_settings=settings,
        )
        trace.stage_done(
            "persist",
            "Episode persisted",
            counts={"episode": 1, "chunk": 1},
            snippet=result.episode.id,
        )
        trace.stage_done("fts", "Chunk indexed for exact search", counts={"chunks": 1})
        trace.stage_done(
            "vector",
            "Semantic vector stored" if result.embedded else "Semantic vector skipped",
            status="ok" if result.embedded else "warning",
            counts={"embedded": result.embedded},
        )
        trace.stage_done(
            "candidates",
            "Extraction candidates created"
            if result.candidates_written
            else "No extraction candidates created",
            counts={"candidates_written": result.candidates_written},
        )
        trace.graph_delta(
            object_type="episode",
            object_id=result.episode.id,
            action="created",
            label="Episode ingested",
            counts={"chunk_id": result.chunk.id},
        )
        response = IngestEpisodeResponse(
            episode_id=result.episode.id,
            chunk_id=result.chunk.id,
            redacted_text=result.episode.raw_text,
            redacted_kinds=result.redacted_kinds,
            created_at=result.episode.created_at,
            embedded=result.embedded,
            auto_promoted_decisions=result.auto_promoted_decisions,
            auto_promoted_rules=result.auto_promoted_rules,
            auto_promoted_core=result.auto_promoted_core,
            candidates_written=result.candidates_written,
        )
        trace.stage_done("response", "Ingest response ready", counts={"embedded": result.embedded})
        return response
