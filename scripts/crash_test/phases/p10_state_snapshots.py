"""Phase 10: canonical snapshots via memory_write."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P10StateSnapshots(Phase):
    name = "p10_state_snapshots"
    description = "canonical snapshot write/read smoke."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        response = post(
            state.client,
            "/memory/write",
            {
                "workspace_id": state.workspace_id,
                "kind": "snapshot",
                "payload": {
                    "snapshot_key": "qa-snap-1",
                    "title": "QA snapshot",
                    "source_label": "crash-test",
                    "total_rows": 1,
                },
            },
        )
        data = response.get("data") or {}
        result.assert_true("snapshot write returns id", bool(data.get("id")))
        result.assert_eq("snapshot projection kind", data.get("kind"), "snapshot")
        return result
