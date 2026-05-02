"""Procedural rules — durable agent operating instructions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProceduralRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    rule_text: str = Field(min_length=1)
    scope: str = "workspace"
    source_episode_id: str | None = None
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    importance: float = Field(default=0.75, ge=0.0, le=1.0)


class ProceduralRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    rule_text: str
    scope: str
    active: bool
    source_episode_id: str | None
    confidence: float
    importance: float
    created_at: str
    updated_at: str
