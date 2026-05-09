"""1.8.0: narrative file digest — collapsed view of a file's chunks,
edges, and recent versions, stored as one durable record per
(workspace_id, file_path).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FileDigestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    file_path: str = Field(min_length=1, max_length=400)
    language: str | None = Field(default=None, max_length=32)
    chunk_count: int = Field(default=0, ge=0)
    symbol_count: int = Field(default=0, ge=0)
    inbound_edge_count: int = Field(default=0, ge=0)
    outbound_edge_count: int = Field(default=0, ge=0)
    versions_recent: int = Field(default=0, ge=0)
    narrative: str = Field(default="", max_length=8000)
    structured: dict[str, Any] = Field(default_factory=dict)
    last_indexed_at: str


class FileDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    file_path: str
    language: str | None
    chunk_count: int
    symbol_count: int
    inbound_edge_count: int
    outbound_edge_count: int
    versions_recent: int
    narrative: str
    structured: dict[str, Any] = Field(default_factory=dict)
    last_indexed_at: str
    updated_at: str
