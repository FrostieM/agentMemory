"""Phase 13: compact + compact_trigger probes."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P13Compaction(Phase):
    name = "p13_compaction"
    description = (
        "compact endpoint runs without error; compact_trigger probe is a no-op when threshold=0."
    )

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        compact = post(
            state.client,
            "/memory/compact",
            {"workspace_id": state.workspace_id},
        )
        result.assert_true(
            "compact returns dict",
            isinstance(compact, dict),
            hint=str(compact)[:120],
        )

        trigger = post(
            state.client,
            "/memory/compact_trigger",
            {"workspace_id": state.workspace_id},
        )
        # threshold=0 is the default → never fires
        result.assert_true(
            "compact_trigger returns dict",
            isinstance(trigger, dict),
            hint=str(trigger)[:120],
        )
        return result
