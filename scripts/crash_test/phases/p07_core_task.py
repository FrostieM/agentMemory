"""Phase 07: task lifecycle through canonical memory_write."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P07CoreTask(Phase):
    name = "p07_core_task"
    description = "task write + compact get/brief retrieval."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        out = post(
            state.client,
            "/memory/write",
            {
                "workspace_id": state.workspace_id,
                "kind": "task",
                "payload": {
                    "task_id": "qa-task-1",
                    "goal": "Run all crash-test phases",
                    "status": "in_progress",
                    "current_plan": ["seed", "verify", "teardown"],
                    "completed_steps": ["seed"],
                    "next_action": "verify",
                    "blockers": [],
                    "files_in_scope": ["scripts/crash_test/"],
                },
            },
        )["data"]
        result.assert_true("state_id returned", bool(out.get("state_id") or out.get("task_id")))

        detail = post(
            state.client,
            "/memory/search",
            {
                "workspace_id": state.workspace_id,
                "query": "phase plan",
                "kinds": ["task"],
                "limit": 5,
            },
        )
        text = str(detail)
        result.assert_in("task goal in compact search", "Run all crash-test phases", text)
        return result
