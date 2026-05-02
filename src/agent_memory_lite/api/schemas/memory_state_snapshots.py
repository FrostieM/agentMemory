"""Wire schemas for memory-state snapshot endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SaveStateSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StateSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    workspace_id: str
    name: str
    taken_at: str
    counts: dict[str, int]
    metadata: dict[str, Any]
    created_at: str


class ListStateSnapshotsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    limit: int = Field(default=20, ge=1, le=200)


class ListStateSnapshotsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshots: list[StateSnapshotResponse]


class DiffStateSnapshotsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    before_id: str = Field(min_length=1)
    after_id: str = Field(min_length=1)


class DiffStateSnapshotsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before_snapshot_id: str
    after_snapshot_id: str
    before_taken_at: str
    after_taken_at: str
    counts_delta: dict[str, int]
    added: list[str]
    removed: list[str]
    changed: list[str]
