"""Wire-side schemas for episode and file ingestion."""

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
    label: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Optional short visual hint for the live observatory UI. Carries no retrieval weight."
        ),
    )
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
    embedded: bool
    auto_promoted_decisions: int = 0
    auto_promoted_rules: int = 0
    auto_promoted_core: int = 0
    candidates_written: int = 0


class IngestFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    path: str = Field(min_length=1)
    content: str = Field(min_length=0)
    language: str | None = None


class IngestFileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str
    path: str
    chunks_written: int
    skipped: bool
    last_indexed_at: str
