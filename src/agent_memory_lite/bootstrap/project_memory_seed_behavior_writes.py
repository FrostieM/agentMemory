"""Write-discipline behavior_instruction factories for the project-memory seed.

Split out of ``project_memory_seed_behavior.py`` (Phase 1.2 of v2.2
consolidation) to keep modules ≤150 SLOC. These rules govern the act of
writing memory: search before any non-trivial write, and follow every
decision/theory write with a capability link.

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
            "memory_write_decision and memory_write_theory are NOT atomic — "
            "they are step 1 of a two-step write. Step 2 is mandatory: "
            "memory_link_capability("
            "target_type=<decision|theory>, target_id=<just-written id>, "
            "capability_type=<role|skill|playbook>, capability_name=<name>, "
            "relation=<owner|method|validation_playbook|...>). "
            "A decision or theory without a capability link is an INCOMPLETE "
            "write that hygiene_report flags as missing_capability_link and "
            "quality_gate downgrades to degraded. If you cannot pick a "
            "capability with confidence, link to the closest one with "
            "rationale='operator review needed' rather than skipping — a "
            "weak link is recoverable, a missing link is silent debt. "
            "Operator-observed compliance gap 2026-05-09: only 20% of "
            "writes had the step-2 follow-up despite 73% having step-0 "
            "search; this rule exists to close that gap."
        ),
        kind=BehaviorInstructionKind.OPERATING_RULE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.USER_PREFERENCE,
        conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
        rationale=(
            "Live regression in copyBot 2026-05-05: 53 missing_capability_link "
            "findings on ~150 decisions/theories despite the project having 12 "
            "roles, 35 skills, and 15 playbooks defined. After 1.2.4 telemetry "
            "(2026-05-09): search-discipline rule reached 73% follow-up but "
            "capability-link rule stayed at 20%. The rule was advisory; the "
            "1.2.5 rewrite reframes write as a two-step atomic action so "
            "agents stop treating link_capability as optional cleanup."
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


def search_before_write_discipline_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Generic discipline rule: search before any non-trivial write.

    Closes the "agent relied on auto-injected envelope and missed prior
    knowledge" failure mode. The auto-injected ``<memory_context>``
    envelope shows the top-N RRF-fused chunks for the current prompt
    — what didn't fit the budget is invisible. Two cheap searches cost
    microseconds; one missed prior decision costs hours of duplicate
    work or contradicts a superseded design.

    Operator-observed regression 2026-05-05: Codex agents call
    ``memory_search`` 8-12x per session on copyBot; Claude agents call
    it 0-2x and rely on the auto-injected envelope. Result: Claude
    sometimes re-opens architectural questions already settled in
    superseded decisions, and writes near-duplicate decisions.

    Project-AGNOSTIC — applies to any agent using this memory
    subsystem regardless of language or workflow style.
    """
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="Search before write — auto-inject is not exhaustive",
        rule=(
            "Every non-trivial write is a 3-step action: search → write → "
            "link_capability. This rule covers step 0 (search). Before any "
            "non-trivial write or edit, run memory_search with the file path, "
            "error string, or domain term to surface chunks the auto-injected "
            "envelope did not show. Cheap, microseconds. Before an "
            "architectural decision specifically, call memory_list_decisions "
            "with include_superseded=true so prior pivots are visible — the "
            "default historical=false hides them. The envelope shows top-N by "
            "RRF; what is missing is what bites you. Two searches over one "
            "missed prior decision: every time. Pair with the "
            "'Link capability after every decision and theory write' rule for "
            "the step-2 follow-up; together they make every write traceable."
        ),
        kind=BehaviorInstructionKind.OPERATING_RULE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.USER_PREFERENCE,
        conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
        rationale=(
            "Operator observed Claude agents call memory_search 0-2x per "
            "session vs Codex 8-12x. Claude relies on the auto-injected "
            "envelope which is RRF-truncated; missed chunks lead to "
            "duplicate decisions and re-opened architectural questions. "
            "This rule lives in every workspace's <behavior_instructions> "
            "envelope so 'search liberally' is part of the agent's first read."
        ),
        applies_to=[
            "memory_write_decision",
            "memory_write_theory",
            "memory_add_theory_evidence",
            "memory_write_experiment",
            "memory_upsert_concept",
            "memory_upsert_behavior_instruction",
            "before editing a specific file",
            "before architectural decisions",
        ],
        source_episode_id=source_episode_id,
        source_type="seed_bootstrap",
        confidence=0.9,
        active=True,
    )
