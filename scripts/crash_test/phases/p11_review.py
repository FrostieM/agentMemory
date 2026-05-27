"""Phase 11: review_queue candidate surface."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P11Review(Phase):
    name = "p11_review"
    description = "review_queue endpoint responds shape-correctly."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
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
        result.assert_true("review_queue has items list", isinstance(queue.get("items"), list))
        return result
