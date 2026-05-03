"""Phase 07: task_state lifecycle (core_memory + procedural_rules ride on the
ingestion path; task_state has its own endpoint)."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P07CoreTask(Phase):
    name = "p07_core_task"
    description = "task_state update + retrieval; presence in <task_state> envelope."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        out = post(
            state.client,
            "/memory/update_task_state",
            {
                "workspace_id": state.workspace_id,
                "task_id": "qa-task-1",
                "goal": "Run all crash-test phases",
                "status": "in_progress",
                "current_plan": ["seed", "verify", "teardown"],
                "completed_steps": ["seed"],
                "next_action": "verify",
                "blockers": [],
                "files_in_scope": ["scripts/crash_test/"],
            },
        )
        result.assert_true("state_id returned", bool(out.get("state_id") or out.get("task_id")))

        ctx = post(
            state.client,
            "/memory/get_context",
            {
                "workspace_id": state.workspace_id,
                "task_id": "qa-task-1",
                "query": "phase plan",
                "max_tokens": 1500,
            },
        )
        text = str(ctx.get("context_text", ""))
        result.assert_in("envelope shows task_state", "<task_state", text)
        result.assert_in("task goal in envelope", "Run all crash-test phases", text)
        return result
