"""Phase 17: v1.5 — capability maturity counters."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P17V15CapabilityMaturity(Phase):
    name = "p17_v15_capability_maturity"
    description = "record_outcome bumps success_count + writes audit row."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        ids = state.bag.get("capability_ids") or {}
        skill_id = ids.get("skill_id")
        if not skill_id:
            result.skip("no skill seeded")
            return result

        post(
            state.client,
            "/memory/capability/record_outcome",
            {
                "workspace_id": state.workspace_id,
                "kind": "skill",
                "capability_id": skill_id,
                "success": True,
            },
        )
        row = state.conn.execute(
            "SELECT success_count, failure_count FROM agent_skills WHERE id = ?",
            (skill_id,),
        ).fetchone()
        result.assert_eq("success_count incremented", int(row[0]), 1)
        result.assert_eq("failure_count untouched", int(row[1]), 0)

        audit = state.conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'capability.outcome_recorded' "
            "AND target_id = ?",
            (skill_id,),
        ).fetchone()
        result.assert_eq("audit row written", int(audit[0]), 1)
        return result
