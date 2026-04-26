"""POST /memory/ingest_episode — write an episode + chunk + FTS row + vector."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    EmbeddingProviderDep,
    SettingsDep,
    VectorStoreDep,
)
from agent_memory_lite.api.schemas.ingest import (
    IngestEpisodeRequest,
    IngestEpisodeResponse,
)
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
    episode_in = EpisodeIn(
        workspace_id=body.workspace_id,
        session_id=body.session_id,
        task_id=body.task_id,
        source_type=body.source_type,
        raw_text=body.raw_text,
        summary=body.summary,
        trust_level=body.trust_level,
        importance=body.importance,
        confidence=body.confidence,
        metadata=body.metadata,
    )
    result = ingest_episode(
        conn,
        episode_in,
        embedding_provider=provider,
        vector_store=store,
        auto_promote_settings=settings,
    )
    return IngestEpisodeResponse(
        episode_id=result.episode.id,
        chunk_id=result.chunk.id,
        redacted_text=result.episode.raw_text,
        redacted_kinds=result.redacted_kinds,
        created_at=result.episode.created_at,
        auto_promoted_decisions=result.auto_promoted_decisions,
        auto_promoted_rules=result.auto_promoted_rules,
        auto_promoted_core=result.auto_promoted_core,
    )
