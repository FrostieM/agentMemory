"""File domain models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FileRecordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    path: str = Field(min_length=1)
    language: str | None = None
    content_hash: str
    size_bytes: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    path: str
    language: str | None
    content_hash: str
    size_bytes: int
    last_indexed_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
