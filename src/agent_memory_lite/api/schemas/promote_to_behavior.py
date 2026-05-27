"""Wire schemas for v1.10 promote-memory-candidate-to-behavior endpoint.

Used by ``POST /memory/promote_candidate_to_behavior`` to convert a
``candidates(kind=correction)`` row into a durable behavior. The conversion is operator-driven —
the trust gate stays intact and corrections never auto-promote.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)


class PromoteCandidateToBehaviorRequest(BaseModel):
    """Operator-supplied parameters for promotion.

    The candidate's ``subject`` is used as the ``rule`` text by default;
    pass ``rule_text_override`` to refine the wording before persisting.
    All behavior-instruction enum fields default to sensible values
    (operating_rule / workspace / user_preference / current_user_wins)
    so a one-click promote is enough; the operator can override any
    of them when finer control is needed.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    candidate_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=160)
    # ``rule`` text is rendered into every active envelope; bound the
    # operator-supplied override so a 100-KB string can't bloat the
    # context surface. 2000 chars is ~500 tokens — comfortably above
    # any reasonable behavior rule, while bounded enough that prompt-
    # injection content can't expand silently.
    rule_text_override: str | None = Field(default=None, min_length=1, max_length=2000)
    rationale: str = Field(default="", max_length=2000)
    kind: BehaviorInstructionKind = BehaviorInstructionKind.OPERATING_RULE
    scope: BehaviorInstructionScope = BehaviorInstructionScope.WORKSPACE
    priority: BehaviorInstructionPriority = BehaviorInstructionPriority.USER_PREFERENCE
    conflict_policy: BehaviorConflictPolicy = BehaviorConflictPolicy.CURRENT_USER_WINS
    applies_to: list[str] = Field(default_factory=list)
    decided_by: str | None = None
    # When True, the resulting behavior is also pinned so it
    # rides the active-context envelope regardless of query relevance.
    # Useful for critical operating rules ("never modify production
    # without confirmation") where missing the rule on a noisy query
    # would be the worst-case outcome.
    pinned: bool = False
    # When False (default), promotion refuses to replace an existing
    # active behavior with the same name in the workspace.
    # Pass True to replace, with full audit trail preserved.
    overwrite: bool = False


class PromoteCandidateToBehaviorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    behavior_instruction_id: str
    status: str = "promoted"
