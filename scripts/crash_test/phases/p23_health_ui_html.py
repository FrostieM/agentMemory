"""Phase 23: /health structure + /ui HTML smoke.

This is the always-runnable UI sanity check. The full browser-rendered
check lives in p24_ui_browser (Playwright), which is skipped if
Playwright is not installed.
"""

from __future__ import annotations

import httpx
from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult


class P23HealthUiHtml(Phase):
    name = "p23_health_ui_html"
    description = "/health full shape; /ui returns HTML 200 with the SSE script tag."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        health = state.client.get("/health", timeout=10.0).json()
        for key in (
            "status",
            "version",
            "db",
            "workspace_id",
            "embedding_backend",
            "embedding_model",
            "vector_backend",
            "llm_backend",
            "llm_model",
            "applied_migrations",
            "retrieval_integrity",
        ):
            result.assert_in(f"/health.{key}", key, health)

        # /ui — HTML 200 with expected fragments.
        ui = state.client.get("/ui", timeout=10.0)
        result.assert_eq("/ui status", ui.status_code, 200)
        body = ui.text
        for fragment in (
            "<html",
            "<script",
            "/ui/app.js",  # SPA bootstrap
            "graphSvg",  # main SVG element id
        ):
            result.assert_in(f"/ui body has {fragment}", fragment, body)

        # /memory/ui/state must respond JSON 200.
        ui_state = state.client.get(
            "/memory/ui/state",
            params={"workspace_id": state.workspace_id},
            timeout=10.0,
        )
        try:
            payload = ui_state.json()
        except (ValueError, httpx.HTTPError):
            payload = {}
        result.assert_eq("/memory/ui/state status", ui_state.status_code, 200)
        result.assert_true(
            "/memory/ui/state returns JSON dict",
            isinstance(payload, dict),
            hint=str(payload)[:120],
        )
        return result
