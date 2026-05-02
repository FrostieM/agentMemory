"""Temporal facts.

A fact is a (subject, relation, object|literal) triple with first-class
temporal validity. Conflicting writes do not delete the prior fact — they
close `valid_to` and link via `invalidated_by_fact_id`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import TrustLevel


class FactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    subject_entity_id: str
    relation: str = Field(min_length=1)
    object_entity_id: str | None = None
    literal_value: str | None = None
    fact_text: str = Field(min_length=1)
    source_episode_id: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    trust_level: TrustLevel = TrustLevel.AGENT_OBSERVED
    metadata: dict[str, Any] = Field(default_factory=dict)


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    subject_entity_id: str
    relation: str
    object_entity_id: str | None
    literal_value: str | None
    fact_text: str
    source_episode_id: str
    confidence: float
    importance: float
    trust_level: TrustLevel
    observed_at: str
    valid_from: str
    valid_to: str | None
    invalidated_by_fact_id: str | None
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
