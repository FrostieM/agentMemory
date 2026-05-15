"""PreToolUse-enforcement factories for trail-aware mechanical rules.

These rules require knowledge of the session's prior tool calls (which
the hook reads from Claude Code's transcript JSONL). They are still
``enforcement:mechanical`` — the trail check is deterministic and
fast — but the dispatcher routes them through detectors that take the
trail as an extra parameter.

Sibling module
``project_memory_seed_behavior_pretooluse_payload.py`` holds payload-
only rules that need no trail.
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


def read_before_edit_pretooluse_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Block Edit/Write without prior Read or memory_file_digest in the session."""
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="pretooluse:read-before-edit",
        rule=(
            "TRIGGER: Edit, Write, or NotebookEdit is about to fire AND the "
            "session has not yet called Read, memory_file_digest, or "
            "memory_find_symbols.\n\n"
            "ACTION: call memory_file_digest(file_path) FIRST. If the digest "
            "returns found=false, call memory_ingest_file then re-attempt.\n\n"
            "KEY INVARIANT: an Edit without prior context-load is patching by "
            "fragment. The hook blocks the Edit until the agent has loaded "
            "context for THIS session."
        ),
        rationale=(
            "Companion enforcement to the Memory-first reminder. The reminder "
            "teaches; the hook stops the failure mode where the agent edits "
            "blindly when the reminder is forgotten under task pressure."
        ),
        applies_to=[
            "enforcement:mechanical",
            "mechanical:read-before-edit",
            "before Edit tool",
            "before Write tool",
        ],
        source_episode_id=source_episode_id,
        **_COMMON_KWARGS,
    )


def search_before_arch_write_pretooluse_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Block memory_write_decision/theory without prior memory_search in session."""
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="pretooluse:search-before-architectural-write",
        rule=(
            "TRIGGER: memory_write_decision, memory_write_theory, or "
            "memory_record_with_evidence is about to fire AND the session "
            "has not yet called memory_search, memory_list_decisions, "
            "memory_list_theories, or memory_get_context.\n\n"
            "ACTION: call memory_list_decisions(query=..., "
            "include_superseded=true) for an architectural choice, OR "
            "memory_list_theories for a research claim, FIRST. Then proceed.\n\n"
            "KEY INVARIANT: an architectural write without prior-art search "
            "risks contradicting an existing decision or re-opening a "
            "settled question. The hook blocks the write until the agent "
            "has checked."
        ),
        rationale=(
            "Companion to Search before write reminder. Hook converts the "
            "reminder into an enforcement so the agent cannot ship a fresh "
            "decision that contradicts a superseded prior one."
        ),
        applies_to=[
            "enforcement:mechanical",
            "mechanical:search-before-arch",
            "memory_write_decision",
            "memory_write_theory",
            "memory_record_with_evidence",
        ],
        source_episode_id=source_episode_id,
        **_COMMON_KWARGS,
    )
