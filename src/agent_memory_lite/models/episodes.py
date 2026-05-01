"""Episode domain models.

`EpisodeIn` is the wire-friendly input (no server-set fields). `Episode` is the
fully-hydrated record used inside the service.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import EpisodeSource, TrustLevel


class EpisodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    session_id: str | None = None
    task_id: str | None = None
    source_type: EpisodeSource
    raw_text: str
    summary: str | None = None
    label: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Optional short visual hint shown in the UI. Carries no retrieval "
            "weight: FTS/vector/scoring still use `raw_text` only."
        ),
    )
    trust_level: TrustLevel = TrustLevel.UNKNOWN
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Episode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    session_id: str | None
    task_id: str | None
    source_type: EpisodeSource
    raw_text: str
    summary: str | None
    label: str | None = None
    trust_level: TrustLevel
    importance: float
    confidence: float
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
