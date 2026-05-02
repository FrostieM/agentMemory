"""Wire schemas for the review queue + compact-trigger endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReviewQueueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    limit_per_kind: int = Field(default=10, ge=1, le=50)


class ReviewQueueItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    target_type: str
    target_id: str
    summary: str
    severity: str
    created_at: str
    details: dict[str, Any]


class ReviewQueueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    generated_at: str
    counts: dict[str, int]
    items: list[ReviewQueueItemResponse]


class CompactTriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"


class CompactTriggerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    total: int
    stale: int
    threshold: int
    triggered: bool = False
    event_written: bool = False
