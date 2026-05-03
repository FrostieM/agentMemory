"""Phase 25: 1.1.0 improvements end-to-end.

Three feature loops that the runner enables via env:

* MEMORY_IMPLICIT_FEEDBACK_ENABLED   — archive/promote/link write
                                       implicit feedback rows
* MEMORY_SENTINEL_AUTORUN_HOURS=tiny — get_context schedules sentinel
                                       runs on traffic
* (always on)                        — get_context envelope contains
                                       <pending_review> when there's a
                                       pending decision/insight candidate
"""

from __future__ import annotations

import time

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P25V2Improvements(Phase):
    name = "p25_v2_improvements"
    description = (
        "Implicit feedback rows / pending_review envelope block / on-traffic sentinel hook."
    )

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        ws = state.workspace_id

        # --- Implicit feedback: archive a chunk → -1.0 row with source=implicit_archive
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
            {"workspace_id": ws, "kind": "chunk", "id": chunk_id, "archive": True},
        )
        after = self._feedback_count(state, chunk_id, "implicit_archive")
        result.assert_eq("implicit_archive feedback row written", after - before, 1)
        usefulness = self._latest_usefulness(state, chunk_id, "implicit_archive")
        result.assert_eq("implicit_archive usefulness", usefulness, -1.0)

        # --- pending_review envelope block: a v1.7 phase has already created
        # one decision_candidate. Confirm get_context envelope contains the block.
        pending_count = state.conn.execute(
            "SELECT COUNT(*) FROM decision_candidates WHERE workspace_id = ? AND status='pending'",
            (ws,),
        ).fetchone()[0]
        ctx = post(
            state.client,
            "/memory/get_context",
            {"workspace_id": ws, "query": "any topic", "max_tokens": 1500},
        )
        text = str(ctx.get("context_text", ""))
        if int(pending_count) > 0:
            result.assert_in("envelope contains <pending_review>", "<pending_review", text)
        else:
            # No pending candidates → block must NOT appear (envelope parity).
            result.assert_true(
                "no <pending_review> when nothing pending",
                "<pending_review" not in text,
            )

        # /health.pending_review surfaces the count
        health = state.client.get("/health", timeout=10.0).json()
        review = health.get("pending_review", {})
        result.assert_in("/health.pending_review present", "decision_candidates", review)
        result.assert_in("/health.pending_review present", "insight_candidates", review)
        result.assert_in("/health.pending_review present", "total", review)

        # --- On-traffic sentinel hook: autorun_hours is tiny, so each
        # get_context call should mark last_sentinel_run_at within a few
        # seconds (no yaml file → scheduler stamps timestamp anyway).
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
            # New get_context calls re-trigger the scheduler on each request.
            post(
                state.client,
                "/memory/get_context",
                {"workspace_id": ws, "query": "wake scheduler", "max_tokens": 600},
            )
            time.sleep(0.5)
        result.assert_true(
            "sentinel scheduler stamped last_sentinel_run_at",
            last_run is not None,
            hint="scheduler thread did not stamp within 6s",
        )

        # --- Hub-mode header-routing smoke check.
        # Send get_context with an explicit X-Memory-DB-Path header pointing
        # at the same DB. This exercises the path the hub-mode middleware
        # uses (per-call DB resolution). Pre-fix, the scheduler ignored the
        # request-scoped DB and stamped settings.db_path; post-fix it reads
        # PRAGMA database_list off the connection.
        headers = {"X-Memory-DB-Path": str(state.db_path)}
        ctx_routed = state.client.post(
            "/memory/get_context",
            json={"workspace_id": ws, "query": "header routing", "max_tokens": 600},
            headers=headers,
            timeout=10.0,
        )
        result.assert_eq(
            "header-routed get_context returns 200",
            ctx_routed.status_code,
            200,
        )
        # The envelope must still include the workspace's pending_review
        # block (or none, if pending count is zero) — never error out.
        routed_text = str(ctx_routed.json().get("context_text", ""))
        result.assert_true(
            "header-routed envelope is well-formed",
            "<memory_context>" in routed_text and "</memory_context>" in routed_text,
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
