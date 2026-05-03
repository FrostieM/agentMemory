"""Phase 14: workspace registry + cross-workspace asymmetric isolation reads.

Asymmetric isolation requires strict mode + project mode env vars set.
The crash test runs the HTTP service in *non-strict* mode (so we can
exercise everything from one workspace), so this phase only verifies the
registry surface.
"""

from __future__ import annotations

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import get


class P14Workspace(Phase):
    name = "p14_workspace"
    description = "GET /memory/workspaces returns the test workspace registered."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        out = get(state.client, "/memory/workspaces")
        workspaces = out.get("workspaces") or out.get("items") or []
        ids = {w.get("workspace_id") or w.get("id") for w in workspaces if isinstance(w, dict)}
        result.assert_in("registry contains test workspace", state.workspace_id, ids)
        return result
