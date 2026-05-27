"""Phase 00: setup — fresh workspace, bootstrap DB, register, start HTTP."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import get


class P00Setup(Phase):
    name = "p00_setup"
    description = "Verify fresh workspace + HTTP service responding."
    fatal_on_failure = True

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        # Service liveness.
        health = get(state.client, "/health")
        result.assert_eq("health.status", health.get("status"), "ok")
        result.assert_eq("health.db", health.get("db"), "ok")
        # Fresh installs use the squashed canonical v3 init migration.
        applied = set(health.get("applied_migrations") or [])
        result.assert_in("applied_migrations", "0001_init", applied)
        # Workspace is the test one.
        result.assert_eq("health.workspace_id", health.get("workspace_id"), state.workspace_id)
        # Fresh DB: zero rows in canonical core tables.
        for table in ("episodes", "decisions", "theories", "behaviors"):
            count = state.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE workspace_id = ?", (state.workspace_id,)
            ).fetchone()[0]
            result.assert_eq(f"fresh:{table}_count", int(count), 0)
        return result
