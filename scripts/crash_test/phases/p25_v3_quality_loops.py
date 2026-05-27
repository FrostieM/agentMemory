"""Phase 25: v3 quality loops end-to-end.

The runner enables the feedback and sentinel loops via env. This phase checks
implicit feedback rows, pending-review health metadata, on-traffic sentinel
stamping through compact reads, and hub-mode header routing.
"""

from __future__ import annotations

import time

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import get, post


class P25V3QualityLoops(Phase):
    name = "p25_v3_quality_loops"
    description = "Implicit feedback / pending-review health / compact-read sentinel hook."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        ws = state.workspace_id

        chunk_row = state.conn.execute(
            "SELECT id FROM chunks WHERE workspace_id = ? AND COALESCE(is_archived,0)=0 LIMIT 1",
            (ws,),
        ).fetchone()
        if chunk_row is None:
            result.skip("no live chunk to archive")
            return result
        chunk_id = str(chunk_row[0])
        before = self._feedback_count(state, chunk_id, "implicit_archive")
        post(
            state.client,
            "/memory/archive",
            {
                "workspace_id": ws,
                "kind": "chunk",
                "id": chunk_id,
                "reason": "crash-test fixture",
            },
        )
        after = self._feedback_count(state, chunk_id, "implicit_archive")
        result.assert_eq("implicit_archive feedback row written", after - before, 1)
        usefulness = self._latest_usefulness(state, chunk_id, "implicit_archive")
        result.assert_eq("implicit_archive usefulness", usefulness, -1.0)

        health = state.client.get("/health", timeout=10.0).json()
        review = health.get("pending_review", {})
        result.assert_in("/health.pending_review present", "decision_reviews", review)
        result.assert_in("/health.pending_review present", "insight_reviews", review)
        result.assert_in("/health.pending_review present", "correction_reviews", review)
        result.assert_in("/health.pending_review present", "total", review)

        deadline = time.time() + 6.0
        last_run = None
        while time.time() < deadline:
            row = state.conn.execute(
                "SELECT value FROM workspace_meta WHERE workspace_id = ? AND key = 'last_sentinel_run_at'",
                (ws,),
            ).fetchone()
            if row is not None:
                last_run = str(row[0])
                break
            get(
                state.client,
                "/memory/brief",
                {"workspace_id": ws, "task": "wake scheduler", "max_tokens": 600},
            )
            time.sleep(0.5)
        result.assert_true(
            "sentinel scheduler stamped last_sentinel_run_at",
            last_run is not None,
            hint="scheduler thread did not stamp within 6s",
        )

        headers = {"X-Memory-DB-Path": str(state.db_path)}
        routed = state.client.get(
            "/memory/brief",
            params={"workspace_id": ws, "task": "header routing", "max_tokens": 600},
            headers=headers,
            timeout=10.0,
        )
        result.assert_eq("header-routed brief returns 200", routed.status_code, 200)
        data = routed.json().get("data", {})
        result.assert_true(
            "header-routed brief has body",
            bool(isinstance(data, dict) and str(data.get("body_md", "")).strip()),
        )
        return result

    @staticmethod
    def _feedback_count(state: CrashTestState, source_id: str, source: str) -> int:
        row = state.conn.execute(
            """
            SELECT COUNT(*) FROM memory_usage_feedback
            WHERE workspace_id = ? AND source_id = ? AND source = ?
            """,
            (state.workspace_id, source_id, source),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _latest_usefulness(state: CrashTestState, source_id: str, source: str) -> float:
        row = state.conn.execute(
            """
            SELECT usefulness FROM memory_usage_feedback
            WHERE workspace_id = ? AND source_id = ? AND source = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (state.workspace_id, source_id, source),
        ).fetchone()
        return float(row[0]) if row else 0.0
