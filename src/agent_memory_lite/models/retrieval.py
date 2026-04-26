"""Retrieval domain types.

Wire types (request/response for `/memory/get_context`) live in
`api/schemas/context.py`. These domain types are what the retrieval pipeline
moves around internally.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    session_id: str | None = None
    task_id: str | None = None
    query: str = Field(min_length=1)
    files_in_scope: list[str] = Field(default_factory=list)
    max_tokens: int = Field(default=3500, ge=200, le=32000)
    historical: bool = False


class RetrievalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    source: str  # "fts" | "vector" | "graph"
    text: str
    path: str = ""
    summary: str | None = None
    raw_score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


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
