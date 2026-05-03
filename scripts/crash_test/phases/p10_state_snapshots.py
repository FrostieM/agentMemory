"""Phase 10: memory_state_snapshots — point-in-time digest + diff."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P10StateSnapshots(Phase):
    name = "p10_state_snapshots"
    description = "snapshot_save / snapshot_list / snapshot_diff round-trip."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        first = post(
            state.client,
            "/memory/snapshot_save",
            {"workspace_id": state.workspace_id, "name": "qa-snap-1"},
        )
        before_id = first.get("snapshot_id") or first.get("id")
        result.assert_true("snapshot_save returns id", bool(before_id))

        # Mutate something between snapshots.
        post(
            state.client,
            "/memory/write_decision",
            {
                "workspace_id": state.workspace_id,
                "title": "Snapshot diff probe",
                "decision_text": "Verifies snapshot_diff catches the new row.",
                "rationale": "QA fixture mutation between snapshots.",
            },
        )

        second = post(
            state.client,
            "/memory/snapshot_save",
            {"workspace_id": state.workspace_id, "name": "qa-snap-2"},
        )
        after_id = second.get("snapshot_id") or second.get("id")
        result.assert_true("second snapshot id", bool(after_id))

        listed = post(
            state.client,
            "/memory/snapshot_list",
            {"workspace_id": state.workspace_id, "limit": 10},
        )
        snapshots = listed.get("snapshots") or listed.get("items") or []
        result.assert_ge("snapshots listed", len(snapshots), 2)

        diff = post(
            state.client,
            "/memory/snapshot_diff",
            {
                "workspace_id": state.workspace_id,
                "before_id": before_id,
                "after_id": after_id,
            },
        )
        added = diff.get("added") or diff.get("added_ids") or []
        result.assert_ge("diff added has at least one row", len(added), 1)
        return result
