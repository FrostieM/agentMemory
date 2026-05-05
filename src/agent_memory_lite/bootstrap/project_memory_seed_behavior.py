"""Generic-discipline behavior_instruction templates for the project-memory seed.

Split from ``project_memory_seed_templates.py`` (1.2.3) to keep the
templates module under the 150-SLOC ceiling. Add new generic discipline
rules here — NOT project-specific personality, language, or style. Each
rule must apply to any agent on any project using this memory subsystem.
"""

from __future__ import annotations

from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)


def link_capability_discipline_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Generic discipline rule: every decision/theory must link to a capability.

    Seeded into every new project memory because the
    "decisions/theories without owner role/skill/playbook" debt is
    universal across projects — copyBot accumulated 50+ orphaned
    research objects in a week. This rule is project-AGNOSTIC (no
    language, personality, or project-specific behavior) and applies
    to any agent using this memory subsystem, so it belongs in the
    seed alongside the bootstrap skill and playbook.

    Operator may override per-workspace by archiving this instruction
    or upserting a stricter replacement. Default ``priority=user_preference``
    + ``conflict_policy=current_user_wins`` so an explicit operator
    instruction in the same chat can always override.
    """
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="Link capability after every decision and theory write",
        rule=(
            "Right after memory_write_decision or memory_write_theory, call "
            "memory_link_capability with the role/skill/playbook that owns "
            "or validates the work. A decision or theory without a capability "
            "link cannot be traced back to who is responsible — over time the "
            "workspace accumulates orphaned research that hygiene_report flags "
            "as missing_capability_link and quality_gate refuses to pass. If no "
            "obvious capability fits, write the decision/theory anyway and surface "
            "the gap to the operator for review rather than skipping the link."
        ),
        kind=BehaviorInstructionKind.OPERATING_RULE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.USER_PREFERENCE,
        conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
        rationale=(
            "Live regression in copyBot 2026-05-05: 53 missing_capability_link "
            "findings on ~150 decisions/theories despite the project having 12 "
            "roles, 35 skills, and 15 playbooks defined. The agent was writing "
            "decisions but not the link_capability follow-up. This rule lives in "
            "every workspace's <behavior_instructions> envelope so the next "
            "agent reads it before the first write of the session."
        ),
        applies_to=[
            "memory_write_decision",
            "memory_write_theory",
            "memory_add_theory_evidence",
            "memory_write_experiment",
        ],
        source_episode_id=source_episode_id,
        source_type="seed_bootstrap",
        confidence=0.9,
        active=True,
    )
