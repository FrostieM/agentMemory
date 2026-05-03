"""Phase 18: v1.6 — cold-memory lifecycle. Endpoint + scanner."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import get


class P18V16Cold(Phase):
    name = "p18_v16_cold"
    description = "/memory/cold_candidates endpoint reachable; scanner returns shape."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        # Force last_retrieved_at into the distant past on one chunk so the
        # scanner has something to find.
        chunk_row = state.conn.execute(
            "SELECT id FROM chunks WHERE workspace_id = ? AND COALESCE(is_archived,0) = 0 LIMIT 1",
            (state.workspace_id,),
        ).fetchone()
        if chunk_row is None:
            result.skip("no chunk available")
            return result
        chunk_id = str(chunk_row[0])
        state.conn.execute(
            "UPDATE chunks SET last_retrieved_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (chunk_id,),
        )
        state.conn.commit()

        out = get(
            state.client,
            "/memory/cold_candidates",
            params={"workspace_id": state.workspace_id, "older_than_days": 30},
        )
        candidate_ids = {c.get("id") for c in (out.get("candidates") or [])}
        result.assert_in("manually-aged chunk surfaces in cold", chunk_id, candidate_ids)
        return result
