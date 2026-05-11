"""Wire schema for ``GET /memory/status``.

Designed to answer the read-side adoption question another AI agent
flagged: "I don't know how much of the workspace is ingested, so I
default to Grep". ``/memory/status`` returns the coverage numbers
in one cheap call so the agent can stop guessing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MemoryCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions_active: int
    decisions_total: int
    theories_active: int
    theories_total: int
    behavior_instructions_active: int
    behavior_instructions_total: int
    capabilities_active_total: int
    episodes_total: int
    chunks_total: int
    insights_new: int
    pending_candidates: int


class CodeMemoryCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: int
    chunks_with_symbols: int
    symbols: int
    edges: int
    soft_edges: int
    versions: int


class AdoptionRatios(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions_with_source_episode_ratio: float
    decisions_linked_to_capability_ratio: float
    behavior_instructions_fired_ratio: float


class MemoryStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    workspace_id: str
    memory: MemoryCounts
    code_memory: CodeMemoryCounts
    adoption: AdoptionRatios
    last_episode_at: str | None
    last_decision_at: str | None
    last_ingest_file_at: str | None
    recent_actions_7d: dict[str, int] = Field(default_factory=dict)
