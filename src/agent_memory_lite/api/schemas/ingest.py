"""Wire-side schemas for episode ingestion."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import EpisodeSource, TrustLevel


class IngestEpisodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    session_id: str | None = None
    task_id: str | None = None
    source_type: EpisodeSource = EpisodeSource.AGENT_ACTION
    raw_text: str = Field(min_length=1)
    summary: str | None = None
    trust_level: TrustLevel = TrustLevel.UNKNOWN
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestEpisodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: str
    chunk_id: str
    redacted_text: str
    redacted_kinds: list[str]
    created_at: str
