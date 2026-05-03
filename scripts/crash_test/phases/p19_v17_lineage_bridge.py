"""Phase 19: v1.7 — theory -> decision-candidate bridge. Trust-gate guard."""

from __future__ import annotations

import os

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import get, post


class P19V17LineageBridge(Phase):
    name = "p19_v17_lineage_bridge"
    description = "Bridge fires only when env flag on; promote endpoint creates real decision."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        # We're running with MEMORY_THEORY_BRIDGE_ENABLED=true (set by runner).
        # Write a validated theory and add 3 supporting evidence rows.
        theory = post(
            state.client,
            "/memory/write_theory",
            {
                "workspace_id": state.workspace_id,
                "title": "Bridge fixture theory",
                "domain": "qa",
                "claim": "Bridge candidate emission triggers at the configured threshold.",
                "predictions": ["candidate row appears at 3rd evidence"],
                "validation_criteria": ["3 supporting evidence rows"],
                "status": "validated",
                "confidence": 0.85,
                "importance": 0.7,
            },
        )
        theory_id = theory.get("theory_id")
        result.assert_true("theory created", bool(theory_id))

        decisions_before = int(
            state.conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE workspace_id = ?",
                (state.workspace_id,),
            ).fetchone()[0]
        )

        for i in range(3):
            post(
                state.client,
                "/memory/add_theory_evidence",
                {
                    "workspace_id": state.workspace_id,
                    "theory_id": theory_id,
                    "kind": "supporting",
                    "summary": f"Supporting evidence #{i}",
                    "metrics": {"n": 10 * (i + 1)},
                    "confidence": 0.8,
                },
            )

        # Bridge should have emitted exactly one pending candidate.
        pending = int(
            state.conn.execute(
                "SELECT COUNT(*) FROM decision_candidates WHERE theory_id = ? AND status = 'pending'",
                (theory_id,),
            ).fetchone()[0]
        )
        result.assert_eq("exactly one pending candidate", pending, 1)

        # CRITICAL trust-gate invariant: decisions table NOT mutated.
        decisions_after = int(
            state.conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE workspace_id = ?",
                (state.workspace_id,),
            ).fetchone()[0]
        )
        result.assert_eq("decisions table untouched by bridge", decisions_after, decisions_before)

        # Promote: now a decision row should be created.
        listed = get(
            state.client,
            "/memory/decision_candidates",
            params={"workspace_id": state.workspace_id, "status": "pending", "limit": 10},
        )
        candidate_id = (listed.get("candidates") or [{}])[0].get("id")
        if candidate_id:
            promo = post(
                state.client,
                f"/memory/decision_candidates/{candidate_id}/promote",
                {"workspace_id": state.workspace_id, "decided_by": "qa-crash-test"},
            )
            result.assert_eq("promote returns status=promoted", promo.get("status"), "promoted")
            result.assert_true("promote returned new decision id", bool(promo.get("decision_id")))
            decisions_final = int(
                state.conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE workspace_id = ?",
                    (state.workspace_id,),
                ).fetchone()[0]
            )
            result.assert_eq(
                "decisions table grew exactly by one after promote",
                decisions_final,
                decisions_before + 1,
            )
        else:
            result.note("no candidate id returned by listing; promote skipped")

        os.environ.setdefault("CRASH_TEST_BRIDGE_OK", "1")  # marker for downstream phases
        return result
