"""Phase 06: behavior_instructions + governance fields + listing."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post, seed_behavior_instruction


class P06Behavior(Phase):
    name = "p06_behavior"
    description = "Upsert + list + governance fields appear in get_context envelope."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        instr_id = seed_behavior_instruction(state.client, workspace_id=state.workspace_id)
        state.bag["behavior_id"] = instr_id
        result.assert_true("instruction id returned", bool(instr_id))

        listed = post(
            state.client,
            "/memory/list_behavior_instructions",
            {"workspace_id": state.workspace_id, "limit": 10},
        )
        items = listed.get("instructions") or []
        result.assert_ge("listed instructions", len(items), 1)
        first = items[0] if items else {}
        result.assert_eq("kind", first.get("kind"), "communication_style")
        result.assert_eq("conflict_policy", first.get("conflict_policy"), "current_user_wins")

        # Get_context envelope must include the behavior_instructions section
        # since the instruction is active.
        ctx = post(
            state.client,
            "/memory/get_context",
            {
                "workspace_id": state.workspace_id,
                "query": "incident report",
                "max_tokens": 2000,
            },
        )
        text = str(ctx.get("context_text", ""))
        result.assert_in("envelope mentions behavior_instructions", "<behavior_instructions", text)
        return result
