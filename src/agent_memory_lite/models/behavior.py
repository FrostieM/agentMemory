"""Persistent behavior and instruction memory models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)


class BehaviorInstructionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    name: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    kind: BehaviorInstructionKind = BehaviorInstructionKind.OPERATING_RULE
    scope: BehaviorInstructionScope = BehaviorInstructionScope.WORKSPACE
    priority: BehaviorInstructionPriority = BehaviorInstructionPriority.USER_PREFERENCE
    rationale: str = ""
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


class BehaviorInstruction(BehaviorInstructionIn):
    id: str
    last_applied_at: str | None = None
    application_count: int = 0
    created_at: str
    updated_at: str
    pinned: bool = False


class BehaviorInstructionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: list[BehaviorInstruction] = Field(default_factory=list)
