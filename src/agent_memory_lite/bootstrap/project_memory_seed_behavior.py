"""Generic-discipline behavior_instruction templates for the project-memory seed.

Split from ``project_memory_seed_templates.py`` (1.2.3) to keep the
templates module under the 150-SLOC ceiling. Add new generic discipline
rules here — NOT project-specific personality, language, or style. Each
rule must apply to any agent on any project using this memory subsystem.

Each factory in this module returns a ``BehaviorInstructionIn`` payload.
The orchestrator in ``project_memory_seed.py`` calls every factory and
upserts each result. To add a new discipline rule:
  1. Define a new ``*_instruction`` factory below.
  2. Re-export it from the public ``__all__`` if other modules need it.
  3. Add the call to ``DISCIPLINE_FACTORIES`` so the orchestrator picks
     it up automatically (no extra orchestrator change needed).
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
            "Before any non-trivial write or edit, run memory_search with the "
            "file path, error string, or domain term to surface chunks the "
            "auto-injected envelope did not show. Cheap, microseconds. Before "
            "an architectural decision specifically, call memory_list_decisions "
            "with include_superseded=true so prior pivots are visible — the "
            "default historical=false hides them. The envelope shows top-N by "
            "RRF; what is missing is what bites you. Two searches over one "
            "missed prior decision: every time."
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


# Registry of all generic-discipline factories; the seed orchestrator
# iterates this list so adding a new rule is one-line: add a factory
# above and append it here. Order is stable to keep upsert-by-name
# behaviour deterministic across runs.
DISCIPLINE_FACTORIES = (
    link_capability_discipline_instruction,
    search_before_write_discipline_instruction,
)
