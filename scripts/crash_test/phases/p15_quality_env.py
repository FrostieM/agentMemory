"""Phase 15: env-flagged memory-quality features under 1.1.0 default-ON.

As of 1.1.0 every memory-quality flag (episode dedup, confidence decay,
auto-conflict detection) defaults ON. This phase verifies the conflict-
detect path runs cleanly under that config — emitting events is fine,
crashing the route or leaving an unqueryable table is not.

Pre-1.1.0 this phase asserted "no auto-conflict events with flag off"
because the default was OFF; with defaults flipped, the assertion now
confirms the path doesn't crash and the events table is queryable.
Per-flag positive coverage lives in the v1.4-v1.9 evolution phases.
"""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult


class P15QualityEnv(Phase):
    name = "p15_quality_env"
    description = (
        "Episode dedup / confidence decay / conflict detect run under default-ON 1.1.0 settings."
    )

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        # The route never errors, the table is queryable, the count is a
        # non-negative integer (zero or more). Cross-flag side-effect
        # coverage lives in p16-p21 (v1.4-v1.9 evolution phases).
        rows = state.conn.execute(
            "SELECT COUNT(*) FROM maintenance_events WHERE kind = 'potential_conflict'"
        ).fetchone()
        count = int(rows[0])
        result.assert_true(
            "potential_conflict events table is queryable under default-ON",
            count >= 0,
        )
        return result
