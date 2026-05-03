"""Wire-side schemas for persistent behavior instructions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.schemas._text_guard import SafeText
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)


class UpsertBehaviorInstructionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    name: SafeText = Field(min_length=1)
    rule: SafeText = Field(min_length=1)
    kind: BehaviorInstructionKind = BehaviorInstructionKind.OPERATING_RULE
    scope: BehaviorInstructionScope = BehaviorInstructionScope.WORKSPACE
    priority: BehaviorInstructionPriority = BehaviorInstructionPriority.USER_PREFERENCE
    rationale: SafeText = ""
    applies_to: list[str] = Field(default_factory=list)
    conflict_policy: BehaviorConflictPolicy = BehaviorConflictPolicy.CURRENT_USER_WINS
    source_episode_id: str | None = None
    source_type: str = Field(default="manual", min_length=1)
    source_id: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    expires_at: str | None = None
    conflict_group: str | None = None
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    active: bool = True


class BehaviorInstructionResponse(UpsertBehaviorInstructionRequest):
    instruction_id: str
    last_applied_at: str | None = None
    application_count: int = 0
    created_at: str
    updated_at: str
    pinned: bool = False


class ListBehaviorInstructionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str | None = None
    kinds: list[BehaviorInstructionKind] | None = None
    include_inactive: bool = False
    limit: int = Field(default=10, ge=1, le=50)
    since: str | None = None
    until: str | None = None


class ListBehaviorInstructionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: list[BehaviorInstructionResponse] = Field(default_factory=list)
