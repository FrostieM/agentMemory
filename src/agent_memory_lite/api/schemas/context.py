"""Wire-side schemas for `memory_get_context`."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GetContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    session_id: str | None = None
    task_id: str | None = None
    query: str = Field(min_length=1)
    files_in_scope: list[str] = Field(default_factory=list)
    max_tokens: int = Field(default=3500, ge=200, le=32000)
    historical: bool = False


class ContextSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    id: str
    score: float
    sources: list[str]
    path: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class GetContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_text: str
    sources: list[ContextSource]
