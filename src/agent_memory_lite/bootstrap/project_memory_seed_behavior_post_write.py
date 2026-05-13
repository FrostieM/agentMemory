"""Post-write enforcement-discipline behavior_instruction factories for the seed.

Split out of ``project_memory_seed_behavior.py`` (v2.2.x cross-project
enforcement, SLOC<=150 cap). This rule fires AFTER a memory write tool
returns, forcing the agent to close the loop (resolve candidates,
link capabilities, repair orphan source_episode_id) in the same turn.

Project-AGNOSTIC: speaks about agent discipline, not about any specific
project's domain.
"""

from __future__ import annotations

from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)


def memory_write_resolve_candidates_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Force candidate resolution + capability linkage in the same turn as the write."""
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="memory-write-is-not-done-until-candidates-resolved",
        rule=(
            "TRIGGER: memory write tool response has candidates_written > 0, "
            "capability_suggestions populated, or source_episode_id == null.\n\n"
            "ACTION (same turn, BEFORE answering operator):\n"
            "  1. LIST every candidate_id and capability_suggestion id in the response.\n"
            "  2. FOR EACH candidate: promote (if evidence supports AND it maps to a "
            "promotable target) OR reject (with one-line reason). Never leave "
            "status=new at end of turn.\n"
            "  3. IF capability_suggestions top-1 has strength >= 0.6 AND no explicit "
            "memory_link_capability was created in this write: call it with top-1.\n"
            "  4. IF source_episode_id == null: either allow_orphan=true with a one-line "
            "reason, OR memory_ingest_episode first and re-issue the write.\n\n"
            "KEY INVARIANT: A write with pending candidates is an INCOMPLETE WRITE. "
            "Review is part of the write, not a later task. 'I'll review later' = "
            "'never reviewed'."
        ),
        kind=BehaviorInstructionKind.OPERATING_RULE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.PROJECT_CONVENTION,
        conflict_policy=BehaviorConflictPolicy.HIGHER_PRIORITY_WINS,
        rationale=(
            "Observed cross-project: 13 pending candidates accumulated in copyBot "
            "review_queue between 2026-05-10 and 2026-05-12 — every one from agent "
            "creating candidates and never coming back to promote/reject them. The "
            "existing 'always-ingest-episode-before-decision' rule closes the BEFORE "
            "side; this rule closes the AFTER side. Together: every write is a "
            "closed-loop transaction."
        ),
        applies_to=[
            "memory_ingest_episode response",
            "memory_write_decision response",
            "memory_record_with_evidence response",
            "memory_write_theory response",
            "candidates_written field",
            "capability_suggestions field",
            "pending review queue",
        ],
        source_episode_id=source_episode_id,
        source_type="seed_bootstrap",
        confidence=0.95,
        active=True,
    )
