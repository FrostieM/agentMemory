"""Wire-side schemas for `memory_search`.

Phase 1 supports `mode=fts` only. Vector and hybrid land in Phase 2.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SearchMode = Literal["fts"]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str = Field(min_length=1)
    mode: SearchMode = "fts"
    limit: int = Field(default=20, ge=1, le=200)


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    score: float
    path: str
    text: str
    summary: str | None = None


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: SearchMode
    hits: list[SearchHit]
