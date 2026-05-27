"""Pydantic wire schemas for the canonical HTTP + MCP surface.

Kept separate from the domain types in ``models/`` so wire shape can
evolve without touching SQL row models. Every response is wrapped in
``{"ok": bool, "data": T | None, "error": dict | None}`` for uniform
error handling at the hook boundary.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_memory_lite.storage.reader import validate_kinds


class Envelope(BaseModel):
    """Uniform response envelope: {ok, data, error}."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    data: dict[str, Any] | list[Any] | None = None
    error: dict[str, str] | None = None


# ============================================================
# Request bodies
# ============================================================


class WriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    agent_id: str = "agent"
    source_episode_id: str | None = None


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    id: str = Field(min_length=1)
    fields: dict[str, Any] = Field(default_factory=dict)
    agent_id: str = "agent"


class PinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    id: str = Field(min_length=1)
    pinned: bool = True
    agent_id: str = "agent"


class ArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    id: str = Field(min_length=1)
    reason: str | None = None
    agent_id: str = "agent"


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    kinds: list[str] | None = None
    limit: int = Field(default=10, ge=1, le=50)
    rerank: bool = Field(
        default=False,
        description=(
            "Run the optional cross-encoder reranker over the initial hit set."
            " Requires the [rerank] extra; falls back silently if unavailable."
        ),
    )

    @field_validator("kinds")
    @classmethod
    def _validate_kinds(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return validate_kinds(value)


class LintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    tool_payload: dict[str, Any] = Field(default_factory=dict)
    transcript_path: str | None = None
