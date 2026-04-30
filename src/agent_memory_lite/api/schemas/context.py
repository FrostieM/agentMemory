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


class ExplainSourceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    rank: int
    raw_score: float
    path: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExplainScoredCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    score: float
    sources: list[str]
    included: bool
    reason: str
    path: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExplainSuppressedBehaviorInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)


class ExplainContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    query: str
    max_tokens: int
    context_tokens: int
    section_counts: dict[str, int]
    source_candidates: list[ExplainSourceCandidate]
    scored_candidates: list[ExplainScoredCandidate]
    included_ids: list[str]
    suppressed_behavior_instructions: list[ExplainSuppressedBehaviorInstruction] = Field(
        default_factory=list
    )
