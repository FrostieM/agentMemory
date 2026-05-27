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
    """Block Edit/Write without prior memory_impact_check in the session."""
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="pretooluse:read-before-edit",
        rule=(
            "TRIGGER: Edit, Write, or NotebookEdit is about to fire AND the "
            "session has not yet called memory_impact_check for this file.\n\n"
            "ACTION: call memory_impact_check(file_path) FIRST and use its "
            "digest, callers, hot symbols, and verdict to scope the edit.\n\n"
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


def impact_check_before_read_pretooluse_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Block Read / Grep without prior memory_impact_check in the session."""
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="pretooluse:impact-check-before-read",
        rule=(
            "TRIGGER: Read or Grep is about to fire on a source file AND the "
            "session has not yet called memory_impact_check.\n\n"
            "ACTION: call memory_impact_check(file_path=<path>) FIRST and use "
            "its digest, callers, hot symbols, and verdict to scope the read. "
            "Read is a fallback for understanding algorithm logic only -- "
            "impact_check is the default for source files.\n\n"
            "KEY INVARIANT: a Read or Grep without prior context-load risks "
            "tunnel vision: the agent edits or grep-walks based on a "
            "partial view of the file's role. The hook blocks the read-side "
            "tool until the agent has loaded context for THIS session."
        ),
        rationale=(
            "Companion enforcement to read-before-edit. The edit-side hook "
            "already enforces impact_check before Edit / Write; this closes "
            "the gap for Read / Grep, which discipline rule 1 also requires."
        ),
        applies_to=[
            "enforcement:mechanical",
            "mechanical:impact-check-before-read",
            "before Read tool",
            "before Grep tool",
        ],
        source_episode_id=source_episode_id,
        **_COMMON_KWARGS,
    )


def search_before_arch_write_pretooluse_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Block architectural memory_write calls without prior memory_search."""
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="pretooluse:search-before-architectural-write",
        rule=(
            "TRIGGER: memory_write(kind=decision) or memory_write(kind=theory) "
            "is about to fire AND the session has not yet called memory_search "
            "for the same topic.\n\n"
            'ACTION: call memory_search(query=..., kinds=["decision"]) for '
            "an architectural choice, OR memory_search(query=..., "
            'kinds=["theory"]) for a research claim, FIRST. Then proceed.\n\n'
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
            "memory_write",
            "memory_write kind=decision",
            "memory_write kind=theory",
        ],
        source_episode_id=source_episode_id,
        **_COMMON_KWARGS,
    )
