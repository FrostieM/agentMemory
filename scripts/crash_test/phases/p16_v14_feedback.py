"""Phase 16: v1.4 — feedback-aware scoring + EWMA aggregator + endpoint."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import get, post


class P16V14Feedback(Phase):
    name = "p16_v14_feedback"
    description = "record_usage_feedback writes a row; feedback_summary endpoint reflects it."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        # Pick any chunk that exists.
        row = state.conn.execute(
            "SELECT id FROM chunks WHERE workspace_id = ? AND COALESCE(is_archived,0) = 0 LIMIT 1",
            (state.workspace_id,),
        ).fetchone()
        if row is None:
            result.skip("no chunks to feedback on")
            return result
        chunk_id = str(row[0])

        post(
            state.client,
            "/memory/record_usage_feedback",
            {
                "workspace_id": state.workspace_id,
                "source_type": "chunk",
                "source_id": chunk_id,
                "query": "feedback for crash test",
                "usefulness": 0.9,
                "task_id": "qa-task-1",
                "notes": "QA fixture",
            },
        )
        # Verify it landed.
        cnt = state.conn.execute(
            "SELECT COUNT(*) FROM memory_usage_feedback WHERE workspace_id = ? AND source_id = ?",
            (state.workspace_id, chunk_id),
        ).fetchone()[0]
        result.assert_eq("feedback row written", int(cnt), 1)

        # /memory/feedback_summary returns shape with signal envelope.
        summary = get(
            state.client,
            "/memory/feedback_summary",
            params={"workspace_id": state.workspace_id, "limit": 50},
        )
        signal = summary.get("signal") or {}
        result.assert_eq("signal.total_rows >= 1", int(signal.get("total_rows", 0)) >= 1, True)
        result.assert_in("signal.enabled key", "enabled", signal)
        return result
