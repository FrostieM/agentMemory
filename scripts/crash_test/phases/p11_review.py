"""Phase 11: review_queue + memory_candidates promote/reject."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P11Review(Phase):
    name = "p11_review"
    description = "list_candidates + review_queue endpoints respond shape-correctly."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        listed = post(
            state.client,
            "/memory/list_candidates",
            {"workspace_id": state.workspace_id, "statuses": ["new"], "limit": 10},
        )
        result.assert_true(
            "list_candidates returns a dict",
            isinstance(listed, dict) and ("candidates" in listed or "items" in listed),
            hint=str(listed)[:120],
        )
        # Queue endpoint should return without crashing even when empty.
        queue = post(
            state.client,
            "/memory/review_queue",
            {"workspace_id": state.workspace_id, "limit_per_kind": 5},
        )
        result.assert_true(
            "review_queue returns a dict",
            isinstance(queue, dict),
            hint=str(queue)[:120],
        )
        return result
