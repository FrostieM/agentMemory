"""Write-side orchestration: episode pipeline (Phase 1), file pipeline (Phase 5),
decision/task/core/procedural writers (Phase 3)."""

from agent_memory_lite.ingestion.episode_pipeline import (
    EpisodeIngestResult,
    ingest_episode,
)

__all__ = ["EpisodeIngestResult", "ingest_episode"]
