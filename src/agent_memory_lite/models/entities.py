"""Graph entity domain models.

An entity is a node in the lite temporal graph. The triple
(`workspace_id`, `type`, `canonical_name`) uniquely identifies a node.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EntityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    type: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    type: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
