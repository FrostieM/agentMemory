"""Workflow-discipline behavior_instruction factories for the project-memory seed.

Split out of ``project_memory_seed_behavior.py`` (Phase 1.2 of v2.2
consolidation) to keep modules ≤150 SLOC. These rules govern the agent's
working-loop discipline: query memory before reading source, never push to
git without explicit permission. Both are seed-pinned (see
``PINNED_DISCIPLINE_FACTORIES`` in ``project_memory_seed_behavior.py``)
so they ride every active brief regardless of query relevance.
"""

from __future__ import annotations

from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.models.enums import (
    BehaviorConflictPolicy,
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
)


def memory_first_before_edit_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Generic discipline rule: query memory before reading/editing source.

    Closes the "agent has the tools but defaults to Read/Grep" adoption
    gap (see dec_ebc1c147bcde92e3 self-audit). The v3
    memory_impact_check surface answers "what does this file do", "who calls
    it", "which symbols are hot", and "how risky is this edit" in one call.
    Read+Grep returns raw text and forces the agent to reconstruct the
    structure manually. The friction is habit, not access; this rule
    lives in every workspace's pinned behavior set so the discipline is
    the agent's first read, not an afterthought.

    Project-AGNOSTIC — applies to any agent on any project that has
    indexed source files.
    """
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="Memory-first before reading or editing source",
        rule=(
            "TRIGGER: before any Read / Grep / Glob / Edit / Write on a source file in "
            "this workspace, and before answering a question that requires reading code.\n\n"
            "ACTION:\n"
            "  1. CALL memory_impact_check(file_path) FIRST. Returns: digest, "
            "callers, hot symbols, verdict, and advisory.\n"
            "  2. IF verdict is high or advisory reports stale/missing digest: narrow "
            "the edit, verify callers explicitly, and keep the patch scoped.\n"
            "  3. FOR EACH function you are about to modify: use the impact_check "
            "callers/hot-symbol output as the first dependency map before reading raw text.\n"
            "  4. FOR symbol lookup or 'who depends on X' questions: prefer the "
            "impact_check/code graph surfaces over bare full-text search.\n\n"
            "KEY INVARIANT: Read/Grep on an un-indexed file is silently making memory "
            "obsolete. The indexed copy is where impact and dependency context lives; "
            "bypass it once and the next agent has no history."
        ),
        kind=BehaviorInstructionKind.WORKFLOW_PREFERENCE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.PROJECT_CONVENTION,
        conflict_policy=BehaviorConflictPolicy.HIGHER_PRIORITY_WINS,
        rationale=(
            "Self-audit 2026-05-10: agent shipped a 4-file UI patch via "
            "Read+Grep, never calling code-memory first. The v3 rewrite makes "
            "memory_impact_check the single first step so digest, callers, hot "
            "symbols, verdict, and advisory are loaded before modifying a file."
        ),
        applies_to=[
            "before Read tool",
            "before Grep tool",
            "before Glob tool",
            "before Edit tool",
            "before Write tool",
            "before editing any file",
            "before modifying a function",
            "code editing workflow",
            "code reading workflow",
        ],
        source_episode_id=source_episode_id,
        source_type="seed_bootstrap",
        confidence=0.9,
        active=True,
    )


def no_unauthorized_git_push_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Generic discipline rule: never git commit/push/CI without permission.

    Operator pushback 2026-05-10 after the agent shipped 5 commits + 5
    pushes for a single UI task on broad instructions like "делай все" /
    "fix it". Phrases that authorize the work do NOT pre-authorize the
    shipping moment. The shipping moment stays with the operator. The
    rule applies even when a commit is "obviously correct" — the cost
    of stopping to ask is one message; the cost of unwanted main-branch
    history is permanent.

    Project-AGNOSTIC — applies to any agent on any git-managed project,
    regardless of language or framework.
    """
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="No git commit/push/CI without explicit operator permission",
        rule=(
            "Never run git commit, git push, or wait on CI runs ("
            "gh run watch, similar) without an explicit operator approval "
            "in chat for THAT specific push. Batching multiple fixes into "
            "one commit is fine, but the moment of 'ship to main' must "
            "always be a deliberate operator decision, not a side-effect "
            "of a broad instruction like 'делай все' / 'fix it' / 'do "
            "everything'. When work is ready to ship, stop, summarize the "
            "staged diff, and ask 'push?' — wait for an affirmative "
            "response before any git write or `gh run watch`. Local "
            "commits are typically permitted but verify if the operator "
            "scope is unclear. Phrases that authorize the work do not "
            "pre-authorize the shipping moment."
        ),
        kind=BehaviorInstructionKind.OPERATING_RULE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.USER_PREFERENCE,
        conflict_policy=BehaviorConflictPolicy.CURRENT_USER_WINS,
        rationale=(
            "Operator pushback 2026-05-10 after agent shipped 5 commits "
            "+ 5 pushes for one UI task: 'ты слишком часто делаешь "
            "commit + push'; then explicit 'не делай commit + push + CI "
            "без моего разрешения'. Locking as seed-pinned rule so every "
            "future workspace inherits the same shipping discipline by "
            "default — discovered the hard way, recorded once, applied "
            "everywhere."
        ),
        applies_to=[
            "git commit",
            "git push",
            "gh run watch",
            "any git write",
            "shipping to main",
        ],
        source_episode_id=source_episode_id,
        source_type="seed_bootstrap",
        confidence=0.95,
        active=True,
    )
