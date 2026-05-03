"""Phase 12: hygiene_report + quality_gate happy paths."""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import get


class P12Hygiene(Phase):
    name = "p12_hygiene"
    description = "Both endpoints respond with status + counts; structure is intact."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        report = get(
            state.client,
            "/memory/hygiene_report",
            params={"workspace_id": state.workspace_id},
        )
        result.assert_in("hygiene status", report.get("status"), {"ok", "warning", "degraded"})
        result.assert_true(
            "hygiene findings list",
            isinstance(report.get("findings"), list),
            hint=str(report)[:120],
        )

        gate = get(
            state.client,
            "/memory/quality_gate",
            params={"workspace_id": state.workspace_id},
        )
        result.assert_in("quality_gate status", gate.get("status"), {"ok", "warning", "degraded"})
        return result
