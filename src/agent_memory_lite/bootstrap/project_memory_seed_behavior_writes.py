"""Write-discipline behavior_instruction factories for the project-memory seed.

Split out of ``project_memory_seed_behavior.py`` (Phase 1.2 of v2.2
consolidation) to keep modules в‰¤150 SLOC. These rules govern the act of
writing memory: search before any non-trivial write, and preserve capability
suggestions from decision/theory writes.

Each factory in this module returns a ``BehaviorInstructionIn`` payload.
Add a new write-discipline rule here, then add the factory to the
aggregator's ``DISCIPLINE_FACTORIES`` tuple in
``project_memory_seed_behavior.py``.
"""

from __future__ import annotations

from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)


def capability_suggestion_discipline_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Generic discipline rule: every decision/theory must record capability context.

    Seeded into every new project memory because the
    "decisions/theories without owner role/skill/playbook" debt is
    universal across projects вЂ” copyBot accumulated 50+ orphaned
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
        name="Record capability suggestion after every decision and theory write",
        rule=(
            "memory_write(kind=decision) and memory_write(kind=theory) may return "
            "capability_suggestions as compact advisory metadata. Inspect them in "
            "the same turn and record the selected capability type/name/relation "
            "in the current task, plan_step, or final episode. If no suggestion "
            "fits, record 'no applicable capability suggestion' with a short "
            "rationale. A decision or theory with ignored suggestions loses "
            "execution context for the next agent. "
            "Operator-observed compliance gap 2026-05-09: only 20% of "
            "writes had the step-2 follow-up despite 73% having step-0 "
            "search; this rule exists to close that gap."
        ),
        kind=BehaviorInstructionKind.OPERATING_RULE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.USER_PREFERENCE,
        conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
        rationale=(
            "Live regression in copyBot 2026-05-05: many decisions/theories lost "
            "capability context despite the project having roles, skills, and "
            "playbooks defined. After 1.2.4 telemetry (2026-05-09): "
            "search-discipline reached 73% follow-up but capability-context "
            "follow-up stayed at 20%. The v3 rewrite keeps capability suggestions "
            "on the compact write response, so agents must preserve that context "
            "without relying on removed write routes."
        ),
        applies_to=[
            "memory_write",
            "memory_write kind=decision",
            "memory_write kind=theory",
            "capability_suggestions field",
        ],
        source_episode_id=source_episode_id,
        source_type="seed_bootstrap",
        confidence=0.9,
        active=True,
    )


def search_before_write_discipline_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Generic discipline rule: search before any non-trivial write.

    Closes the "agent relied on the first brief and missed prior
    knowledge" failure mode. The v3 brief is intentionally compact:
    what did not fit the budget is invisible. Two cheap searches cost
    microseconds; one missed prior decision costs hours of duplicate
    work or contradicts a superseded design.

    Operator-observed regression 2026-05-05: Codex agents call
    ``memory_search`` 8-12x per session on copyBot; Claude agents call
    it 0-2x and rely on the first injected brief. Result: Claude
    sometimes re-opens architectural questions already settled in
    superseded decisions, and writes near-duplicate decisions.

    Project-AGNOSTIC вЂ” applies to any agent using this memory
    subsystem regardless of language or workflow style.
    """
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="Search before write - auto-inject is not exhaustive",
        rule=(
            "Every non-trivial write is a 3-step action: search -> write -> "
            "record capability suggestion. This rule covers step 0 (search). Before any "
            "non-trivial write or edit, run memory_search with the file path, "
            "error string, or domain term to surface rows the compact "
            "brief did not show. Cheap, microseconds. Before an "
            "architectural decision specifically, call memory_search(query=..., "
            'kinds=["decision"]) so prior choices are visible. The brief shows only '
            "a compact top slice; what is missing is what bites you. Two searches over one "
            "missed prior decision: every time. Pair with the "
            "'Record capability suggestion after every decision and theory write' rule for "
            "the step-2 follow-up; together they make every write traceable."
        ),
        kind=BehaviorInstructionKind.OPERATING_RULE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.USER_PREFERENCE,
        conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
        rationale=(
            "Operator observed Claude agents call memory_search 0-2x per "
            "session vs Codex 8-12x. Claude relied on the first injected "
            "brief as if it were exhaustive; missed rows lead to "
            "duplicate decisions and re-opened architectural questions. "
            "This rule lives in every workspace's pinned behavior set so "
            "'search liberally' is part of the agent's first read."
        ),
        applies_to=[
            "memory_write",
            "memory_write kind=decision",
            "memory_write kind=theory",
            "memory_write kind=concept",
            "memory_write kind=behavior",
            "memory_edit",
            "before editing a specific file",
            "before architectural decisions",
        ],
        source_episode_id=source_episode_id,
        source_type="seed_bootstrap",
        confidence=0.9,
        active=True,
    )
