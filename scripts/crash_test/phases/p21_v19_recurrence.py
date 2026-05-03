"""Phase 21: v1.9 — hygiene recurrence + sentinel persistence."""

from __future__ import annotations

import sqlite3

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import get


class P21V19Recurrence(Phase):
    name = "p21_v19_recurrence"
    description = (
        "?persist=true mode dedup-and-increments; recurring_findings filters by threshold."
    )

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        # Run hygiene_report with persist=true twice. The same finding should
        # show recurrence_count >= 2 the second time.
        get(
            state.client,
            "/memory/hygiene_report",
            params={"workspace_id": state.workspace_id, "persist": "true"},
        )
        # Look at any maintenance_event row for this workspace.
        first_count = self._max_recurrence(state.conn, state.workspace_id)

        get(
            state.client,
            "/memory/hygiene_report",
            params={"workspace_id": state.workspace_id, "persist": "true"},
        )
        second_count = self._max_recurrence(state.conn, state.workspace_id)

        # If hygiene found something to persist, second_count should be > first_count.
        if first_count > 0 or second_count > 0:
            result.assert_ge(
                "recurrence_count grows on repeat scans",
                second_count,
                max(first_count, 1),
            )
        else:
            result.note("no hygiene findings to persist (workspace too clean)")

        # /memory/recurring_findings endpoint reachable.
        recurring = get(
            state.client,
            "/memory/recurring_findings",
            params={"workspace_id": state.workspace_id, "threshold": 1},
        )
        result.assert_true(
            "recurring_findings returns dict",
            isinstance(recurring, dict) and "findings" in recurring,
            hint=str(recurring)[:120],
        )

        # /memory/sentinel_trends endpoint reachable.
        trends = get(
            state.client,
            "/memory/sentinel_trends",
            params={"workspace_id": state.workspace_id, "window_days": 7},
        )
        result.assert_true(
            "sentinel_trends returns dict",
            isinstance(trends, dict) and "cases" in trends,
            hint=str(trends)[:120],
        )
        return result

    @staticmethod
    def _max_recurrence(conn: sqlite3.Connection, workspace_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(recurrence_count), 0) FROM maintenance_events WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        return int(row[0]) if row else 0
