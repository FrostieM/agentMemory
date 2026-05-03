"""Phase 03: decisions + supersedes chain + lineage walker."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import get, post, seed_decisions


class P03Decisions(Phase):
    name = "p03_decisions"
    description = "Write decisions; supersedes closes prior; v1.7 lineage walker resolves chain."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        decision_ids = seed_decisions(state.client, workspace_id=state.workspace_id)
        state.bag["decision_ids"] = decision_ids
        result.assert_eq("two decisions written", len(decision_ids), 2)

        first_id, second_id = decision_ids
        # First should be superseded.
        row = state.conn.execute(
            "SELECT status, valid_to FROM decisions WHERE id = ?", (first_id,)
        ).fetchone()
        result.assert_eq("first.status", str(row[0]), "superseded")
        result.assert_true("first.valid_to set", row[1] is not None)
        # Second is active.
        row2 = state.conn.execute(
            "SELECT status FROM decisions WHERE id = ?", (second_id,)
        ).fetchone()
        result.assert_eq("second.status", str(row2[0]), "active")

        # list_decisions returns only active by default.
        listed = post(
            state.client,
            "/memory/list_decisions",
            {"workspace_id": state.workspace_id, "query": "vector", "limit": 10},
        )
        # Schemas surface ids under either "id" or "decision_id"; accept both.
        active_ids = [d.get("id") or d.get("decision_id") for d in (listed.get("decisions") or [])]
        result.assert_in("active list contains second", second_id, active_ids)
        result.assert_true("active list excludes superseded", first_id not in active_ids)

        # v1.7 lineage walker — must pass workspace_id explicitly.
        lineage = get(
            state.client,
            f"/memory/decisions/{second_id}/lineage",
            params={"workspace_id": state.workspace_id},
        )
        chain_ids = [n.get("id") for n in (lineage.get("chain") or [])]
        result.assert_eq("lineage chain length", len(chain_ids), 2)
        result.assert_eq("lineage newest first", chain_ids[0], second_id)
        result.assert_eq("lineage oldest last", chain_ids[-1], first_id)
        return result
