"""Wire-side schemas for capability-to-research links."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_memory_lite.models.enums import (
    CapabilityLinkRelation,
    CapabilityLinkTargetType,
    CapabilityType,
)


class LinkCapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    target_type: CapabilityLinkTargetType
    target_id: str = Field(min_length=1)
    capability_type: CapabilityType
    capability_id: str | None = None
    capability_name: str | None = None
    relation: CapabilityLinkRelation = CapabilityLinkRelation.METHOD
    rationale: str | None = None
    strength: float = Field(default=0.7, ge=0.0, le=1.0)
    source_episode_id: str | None = None

    @model_validator(mode="after")
    def require_capability_reference(self) -> LinkCapabilityRequest:
        if not self.capability_id and not self.capability_name:
            raise ValueError("capability_id or capability_name is required")
        return self


class CapabilityLinkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: str
    workspace_id: str
    target_type: CapabilityLinkTargetType
    target_id: str
    capability_type: CapabilityType
    capability_id: str
    capability_name: str
    relation: CapabilityLinkRelation
    rationale: str | None
    strength: float
    source_episode_id: str | None
    created_at: str
    updated_at: str


class ListCapabilityLinksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    target_type: CapabilityLinkTargetType | None = None
    target_id: str | None = None
    capability_type: CapabilityType | None = None
    capability_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class ListCapabilityLinksResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    links: list[CapabilityLinkResponse]
