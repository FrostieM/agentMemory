"""Core memory: small persistent block the agent always sees.

Holds stable project constraints, security rules, key active decisions. Entries
are gated by trust + confidence + importance thresholds before promotion.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CoreMemoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    key: str = Field(min_length=1)
    value: str = Field(min_length=1)
    source_episode_id: str | None = None
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    importance: float = Field(default=0.85, ge=0.0, le=1.0)


class CoreMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    key: str
    value: str
    source_episode_id: str | None
    confidence: float
    importance: float
    active: bool
    created_at: str
    updated_at: str
