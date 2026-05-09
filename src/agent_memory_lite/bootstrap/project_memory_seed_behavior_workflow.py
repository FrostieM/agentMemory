"""Workflow-discipline behavior_instruction factories for the project-memory seed.

Split out of ``project_memory_seed_behavior.py`` (Phase 1.2 of v2.2
consolidation) to keep modules ≤150 SLOC. These rules govern the agent's
working-loop discipline: query memory before reading source, never push to
git without explicit permission. Both are seed-pinned (see
``PINNED_DISCIPLINE_FACTORIES`` in ``project_memory_seed_behavior.py``)
so they ride every active envelope regardless of query relevance.
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
    gap (see dec_ebc1c147bcde92e3 self-audit). The v1.4 → v2.1.x
    code-memory tools — memory_file_digest, memory_find_symbols,
    memory_graph_neighbors — answer "what does this file do" and "who
    depends on X" in one call. Read+Grep returns raw text and forces the
    agent to reconstruct the structure manually. The friction is habit,
    not access; this rule lives in every workspace's
    ``<behavior_instructions>`` envelope so the discipline is the
    agent's first read, not an afterthought.

    Project-AGNOSTIC — applies to any agent on any project that has
    indexed source files via memory_ingest_file.
    """
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="Memory-first before reading or editing source",
        rule=(
            "Before deeply reading or editing any source file in this "
            "workspace, first call memory_file_digest(file_path) to see "
            "what memory already knows: symbols, in/out edges, narrative, "
            "recent versions. For symbol lookup, prefer memory_find_symbols "
            "over Grep — it is qualified-name-aware. For 'who depends on X', "
            "use memory_graph_neighbors instead of full-text search. Fall "
            "back to bare Read/Grep only when memory yields nothing or the "
            "file isn't indexed yet (memory_ingest_file fixes that). "
            "Skipping this step is the same adoption gap the v1.10 "
            "correction loop was built to catch — and the agent is the "
            "first one who must close it."
        ),
        kind=BehaviorInstructionKind.WORKFLOW_PREFERENCE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.PROJECT_CONVENTION,
        conflict_policy=BehaviorConflictPolicy.HIGHER_PRIORITY_WINS,
        rationale=(
            "Self-audit 2026-05-10: agent shipped a 4-file UI patch via "
            "Read+Grep, never calling memory_file_digest, "
            "memory_find_symbols, or memory_search beforehand. The tools "
            "work; the adoption is the gap. Locking this discipline as a "
            "seed-pinned rule before it calcifies further."
        ),
        applies_to=[
            "before Read tool",
            "before Grep tool",
            "before editing any file",
            "code editing workflow",
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
