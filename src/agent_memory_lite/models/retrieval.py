"""Internal retrieval domain types used before compact projection rendering."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScoredHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    text: str
    path: str = ""
    summary: str | None = None
    score: float
    sources: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
