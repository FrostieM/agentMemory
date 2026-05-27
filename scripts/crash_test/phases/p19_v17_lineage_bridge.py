"""Phase 19: theory to canonical decision candidate bridge."""

from __future__ import annotations

import json
import os

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post

from agent_memory_lite.api.errors import ValidationError
from agent_memory_lite.ingestion.candidate_writer import promote_memory_candidate
from agent_memory_lite.ingestion.theory_evidence_writer import add_theory_evidence
from agent_memory_lite.models.theories import TheoryEvidenceIn


class P19V17LineageBridge(Phase):
    name = "p19_v17_lineage_bridge"
    description = "Bridge emits a review candidate; trust gate blocks low-trust promotion."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        theory = post(
            state.client,
            "/memory/write",
            {
                "workspace_id": state.workspace_id,
                "kind": "theory",
                "payload": {
                    "title": "Bridge fixture theory",
                    "domain": "qa",
                    "claim": "Bridge candidate emission triggers at the configured threshold.",
                    "predictions": ["candidate row appears at 3rd evidence"],
                    "validation_criteria": ["3 supporting evidence rows"],
                    "status": "validated",
                    "confidence": 0.85,
                    "importance": 0.7,
                },
            },
        )
        data = theory.get("data") or {}
        theory_id = data.get("id") or data.get("theory_id")
        result.assert_true("theory created", bool(theory_id))

        decisions_before = int(
            state.conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE workspace_id = ?",
                (state.workspace_id,),
            ).fetchone()[0]
        )

        for i in range(3):
            add_theory_evidence(
                state.conn,
                TheoryEvidenceIn(
                    workspace_id=state.workspace_id,
                    theory_id=theory_id,
                    kind="supporting",
                    summary=f"Supporting evidence #{i}",
                    metrics={"n": 10 * (i + 1)},
                    confidence=0.8,
                ),
            )

        candidate_ids = []
        rows = state.conn.execute(
            """
            SELECT id, metadata_json FROM candidates
            WHERE workspace_id = ? AND kind = 'decision' AND status = 'new'
            """,
            (state.workspace_id,),
        ).fetchall()
        for row in rows:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
            if isinstance(metadata, dict) and metadata.get("theory_id") == theory_id:
                candidate_ids.append(str(row["id"]))
        result.assert_eq("exactly one open candidate", len(candidate_ids), 1)

        decisions_after = int(
            state.conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE workspace_id = ?",
                (state.workspace_id,),
            ).fetchone()[0]
        )
        result.assert_eq("decisions table untouched by bridge", decisions_after, decisions_before)

        if candidate_ids:
            try:
                promote_memory_candidate(state.conn, candidate_id=candidate_ids[0])
            except ValidationError:
                blocked = True
            else:
                blocked = False
            result.assert_true("trust gate blocks low-trust bridge promotion", blocked)
            decisions_final = int(
                state.conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE workspace_id = ?",
                    (state.workspace_id,),
                ).fetchone()[0]
            )
            result.assert_eq(
                "decisions table unchanged after blocked promote",
                decisions_final,
                decisions_before,
            )

        os.environ.setdefault("CRASH_TEST_BRIDGE_OK", "1")
        return result
