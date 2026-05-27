"""Phase 17: capability maturity counters through v3 plan-step outcomes."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult

from agent_memory_lite.capability.usage_tracker import get_maturity_snapshot
from agent_memory_lite.ingestion.plan_step_writer import add_plan_step
from agent_memory_lite.maintenance.plan_outcome_maturity import feed_plan_step_outcomes
from agent_memory_lite.models.enums import (
    CapabilityLinkRelation,
    CapabilityLinkTargetType,
    CapabilityType,
)
from agent_memory_lite.models.plan_step import PlanStepIn
from agent_memory_lite.repositories.capability_links_repo import upsert_capability_link_row


class P17V15CapabilityMaturity(Phase):
    name = "p17_v15_capability_maturity"
    description = "terminal plan steps feed capability success_count through v3."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        ids = state.bag.get("capability_ids") or {}
        skill_id = ids.get("skill_id")
        if not skill_id:
            result.skip("no skill seeded")
            return result

        step = add_plan_step(
            state.conn,
            step_in=PlanStepIn(
                workspace_id=state.workspace_id,
                task_id="crash-test-p17",
                title="Feed capability outcome",
                status="done",
            ),
        )
        step_id = str(step["id"])
        upsert_capability_link_row(
            state.conn,
            link_id=f"cl_{step_id}_{skill_id}",
            workspace_id=state.workspace_id,
            target_type=CapabilityLinkTargetType.PLAN_STEP,
            target_id=step_id,
            capability_type=CapabilityType.SKILL,
            capability_id=skill_id,
            capability_name="End-to-end memory verification",
            relation=CapabilityLinkRelation.METHOD,
            rationale=None,
            strength=0.7,
            source_episode_id=None,
            created_at="2026-05-22T00:00:00Z",
            updated_at="2026-05-22T00:00:00Z",
        )

        fed = feed_plan_step_outcomes(state.conn, workspace_id=state.workspace_id, max_steps=50)
        result.assert_eq("plan-step outcome fed", fed, 1)
        snap = get_maturity_snapshot(
            state.conn,
            workspace_id=state.workspace_id,
            kind="skill",
            capability_id=skill_id,
        )
        result.assert_true("maturity snapshot exists", snap is not None)
        if snap is not None:
            result.assert_eq("success_count incremented", snap.success_count, 1)
            result.assert_eq("failure_count untouched", snap.failure_count, 0)

        audit = state.conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'capability.outcome_recorded' "
            "AND target_id = ?",
            (skill_id,),
        ).fetchone()
        result.assert_eq("audit row written", int(audit[0]), 1)
        return result
