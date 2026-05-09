"""1.6.0: symbol-level version history.

A ``SymbolVersion`` is a snapshot of one symbol's body at a point in
time — the function / method / class as it existed when its file
was ingested. We keep one row per (qualified_name, content_hash)
combination so an agent can ask "what changed in
``paperBot.calculate`` last week?" or "which active symbols had a
SIGNATURE change since deploy?" — the latter being the foundation
of breaking-change detection (paired with ``graph_neighbors`` for
downstream impact).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SymbolVersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    qualified_name: str = Field(max_length=400)
    file_path: str | None = None
    chunk_id: str | None = None
    language: str | None = Field(default=None, max_length=32)
    signature_text: str = Field(max_length=2000)
    signature_hash: str = Field(max_length=64)
    content_hash: str = Field(max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SymbolVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    qualified_name: str
    file_path: str | None
    chunk_id: str | None
    language: str | None
    signature_text: str
    signature_hash: str
    content_hash: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
