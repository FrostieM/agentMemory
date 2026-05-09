"""Chunk domain models.

A chunk is a unit of text bound to a source: an episode, a file, or both.
Embeddings live in the vector store and are keyed by chunk id. `embedding_id`
mirrors that id after a successful upsert/reindex so audits can detect stale
SQLite references without treating SQLite as the vector source of truth.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import ChunkKind


class ChunkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    file_id: str | None = None
    episode_id: str | None = None
    kind: ChunkKind
    text: str
    summary: str | None = None
    label: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Optional short visual hint shown in the UI. Carries no retrieval "
            "weight: FTS/vector/scoring still use `text` only."
        ),
    )
    line_start: int | None = None
    line_end: int | None = None
    symbols: list[str] = Field(default_factory=list)
    # 1.4.0 symbol metadata (only populated for code chunks emitted by
    # the structural chunker; legacy / doc / episode chunks leave them
    # NULL).
    symbol_kind: str | None = Field(default=None, max_length=32)
    qualified_name: str | None = Field(default=None, max_length=400)
    parent_qualified_name: str | None = Field(default=None, max_length=400)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    file_id: str | None
    episode_id: str | None
    kind: ChunkKind
    text: str
    summary: str | None
    label: str | None = None
    line_start: int | None
    line_end: int | None
    symbols: list[str] = Field(default_factory=list)
    symbol_kind: str | None = None
    qualified_name: str | None = None
    parent_qualified_name: str | None = None
    embedding_id: str | None
    importance: float
    confidence: float
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
