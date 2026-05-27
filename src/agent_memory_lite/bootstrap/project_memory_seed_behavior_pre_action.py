"""Pre-action enforcement-discipline behavior_instruction factories for the seed.

Split out of ``project_memory_seed_behavior.py`` (v2.2.x cross-project
enforcement, SLOC<=150 cap) to keep modules at or below 150 SLOC. These
two rules fire BEFORE the agent commits to an action: list applies_to
verbatim, cite prod evidence before any completion claim.

Project-AGNOSTIC: every rule speaks about agent discipline, not about
copyBot strategies, agent-memory-lite internals, or any specific
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


def applies_to_checklist_verbatim_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Force verbatim applies_to listing before actions, blocking categorical reframing."""
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="applies-to-checklist-must-be-stated-verbatim",
        rule=(
            "TRIGGER (before): git commit/push, deploy, restart, memory_write "
            "or memory_edit tool, strategy/env/schema change.\n\n"
            "ACTION (verbatim in reply, not just in head):\n"
            "  1. STATE the action as [VERB] + [OBJECT] + [TARGET].\n"
            "  2. LIST every pinned applies_to in the envelope (bullet, no paraphrasing).\n"
            "  3. MARK each applies_to MATCH / NO-MATCH. Be PESSIMISTIC: if you are "
            "rationalizing 'this is X not Y', that IS the failure mode. Mark MATCH.\n"
            "  4. IF any MATCH: STOP, run the rule's pre-step, paste evidence, then continue.\n\n"
            "KEY INVARIANT: You cannot rename an action to dodge applies_to. Verbatim "
            "listing makes categorical reframing visible to the operator BEFORE the act."
        ),
        kind=BehaviorInstructionKind.OPERATING_RULE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.PROJECT_CONVENTION,
        conflict_policy=BehaviorConflictPolicy.HIGHER_PRIORITY_WINS,
        rationale=(
            "Observed cross-project: agents scan rules but reframe their action to "
            "dodge applies_to. Concrete copyBot case (ep_56d5af9ce2891cf8, 2026-05-12): "
            "agent reframed 'tier ladder threshold change' as 'math fix' to bypass "
            "research-first rule. Verbatim public listing prevents that — the operator "
            "sees the reframe in the reply itself, before the action is executed."
        ),
        applies_to=[
            "git commit",
            "git push",
            "deploy",
            "memory_write",
            "memory_write kind=decision",
            "memory_write kind=behavior",
            "memory_edit",
            "strategy config change",
            "env var change",
            "schema change",
            "categorical reframing prevention",
        ],
        source_episode_id=source_episode_id,
        source_type="seed_bootstrap",
        confidence=0.95,
        active=True,
    )


def verification_claims_cite_evidence_instruction(
    workspace_id: str, source_episode_id: str | None
) -> BehaviorInstructionIn:
    """Force prod-evidence citation before any 'works/deployed/fixed' claim."""
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name="verification-claims-must-cite-prod-evidence",
        rule=(
            "TRIGGER: before any phrase matching works / deployed / fixed / ready / "
            "verified / confirmed / tests pass / no regression / no harm, OR Russian "
            "equivalents работает / сделано / готово / ничего не сломалось.\n\n"
            "ACTION:\n"
            "  1. NAME the evidence source: prod log + timestamp, `curl` output + "
            "HTTP code, prod sqlite row count, `git ls-remote` SHA (NOT `git push`), "
            "/health JSON, or pm2/systemctl status with timestamp.\n"
            "  2. PASTE the evidence inline. Truncate if long; do NOT summarize.\n"
            "  3. IF no evidence yet: write 'claim PENDING verification' and run the "
            "check BEFORE the conclusion sentence.\n\n"
            "KEY INVARIANT: NOT verification of prod behavior: tsc passes, unit tests "
            "green locally, 'I read the code', `git push` succeeded, 'PM2 logs no "
            "error' without grep for the behavior under test. Operator pattern: agent "
            "says 'deployed', actually means 'git push completed'."
        ),
        kind=BehaviorInstructionKind.OPERATING_RULE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=BehaviorInstructionPriority.PROJECT_CONVENTION,
        conflict_policy=BehaviorConflictPolicy.HIGHER_PRIORITY_WINS,
        rationale=(
            "Observed cross-project: agents claim completion based on local compile "
            "signals or code-read confidence, without confirming prod state changed. "
            "Reframes verification as a citation requirement — a claim without a "
            "paste-able evidence source is an incomplete claim. Operator confirmed "
            "2026-05-13 as one of two dominant failure modes."
        ),
        applies_to=[
            "verification claims",
            "deploy confirmation",
            "fix confirmation",
            "ready/works/deployed phrases",
            "post-deploy reports",
            "any completion statement",
        ],
        source_episode_id=source_episode_id,
        source_type="seed_bootstrap",
        confidence=0.95,
        active=True,
    )
