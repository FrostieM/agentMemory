"""PreToolUse-enforcement factories for payload-only mechanical rules.

These rules are determined entirely from the tool's own payload — no
session trail required. The PreToolUse hook (``pre_tool_use_check.py``)
loads them from the workspace's behavior_instructions table and the
mechanical dispatcher routes each rule to its detector via the
``mechanical:*`` tag in ``applies_to``.

Sibling module ``project_memory_seed_behavior_pretooluse_trail.py``
holds trail-aware rules (require prior Read or prior memory_search).
"""

from __future__ import annotations

from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)

_COMMON_KWARGS = {
    "kind": BehaviorInstructionKind.OPERATING_RULE,
    "scope": BehaviorInstructionScope.WORKSPACE,
    "priority": BehaviorInstructionPriority.PROJECT_CONVENTION,
    "conflict_policy": BehaviorConflictPolicy.HIGHER_PRIORITY_WINS,
    "source_type": "seed_bootstrap",
    "confidence": 0.95,
    "active": True,
}


def no_magic_number_in_strategy_pretooluse_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Block Edit/Write that introduces a magic threshold in strategy code."""
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="pretooluse:no-magic-number-in-strategy",
        rule=(
            "TRIGGER: Edit or Write on a strategy/calibrator/tier/edge file "
            "where the diff contains a comparison `identifier op literal` "
            "and the identifier carries threshold semantics (confidence, "
            "threshold, tier, ratio, rate, calibrat, weight, margin, edge).\n\n"
            "ACTION: extract the literal to a named UPPER_SNAKE constant in "
            "the same file, OR move the threshold into a per-strategy adaptive "
            "function (same shape as the other calibrators).\n\n"
            "KEY INVARIANT: a magic numeric threshold in strategy code makes "
            "the rule impossible to test or back-test by strategy id. The "
            "PreToolUse hook blocks the Edit until the literal is named."
        ),
        rationale=(
            "Operator caught 2026-05-15: agent wrote a bare 0.85 threshold in "
            "a calibrator path despite the foreground reminder. The reminder "
            "+ PreToolUse hook is defense-in-depth: agent reads the rule, "
            "but if they skip it the tool call is mechanically blocked."
        ),
        applies_to=[
            "enforcement:mechanical",
            "mechanical:no-magic-number",
            "before Edit tool",
            "before Write tool",
            "strategy code",
            "calibrator code",
        ],
        source_episode_id=source_episode_id,
        **_COMMON_KWARGS,
    )


def decision_must_have_provenance_pretooluse_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Block decision writes that carry no source episode or rationale."""
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="pretooluse:decision-must-have-provenance",
        rule=(
            "TRIGGER: memory_write(kind=decision) is about to fire AND payload "
            "sets neither source_episode_id nor allow_orphan=true AND rationale "
            "is under 30 chars.\n\n"
            "ACTION: either call memory_write(kind=episode) first and pass the "
            "returned episode_id as source_episode_id, OR pass allow_orphan=true "
            "with a >=30-char rationale explaining the source.\n\n"
            "KEY INVARIANT: a decision without provenance is an unprovable "
            "architectural claim. The PreToolUse hook refuses to write one."
        ),
        rationale=(
            "Decisions without provenance accumulate as low-trust claims that "
            "later agents cannot verify or supersede confidently. The "
            "foreground reminder existed; under pressure the agent skipped it. "
            "Hook blocks the write at the tool boundary."
        ),
        applies_to=[
            "enforcement:mechanical",
            "mechanical:decision-provenance",
            "memory_write",
            "memory_write kind=decision",
        ],
        source_episode_id=source_episode_id,
        **_COMMON_KWARGS,
    )
