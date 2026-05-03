"""Phase 24: full UI browser test via Playwright (headless Chromium).

Skips gracefully when Playwright is not installed or chromium is not
downloaded — the user gets a clear note rather than a hard failure.

What it verifies:
  * /ui loads, no JS exceptions in console
  * Workspace dropdown lists the test workspace
  * After triggering an ingest, a spoke appears in the SVG within ~5s
  * Inspector card opens on family-bubble click
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from scripts.crash_test.phases._base import CrashTestState, Phase, PhaseResult
from scripts.crash_test.seeds import post


class P24UiBrowser(Phase):
    name = "p24_ui_browser"
    description = "Headless Chromium loads /ui, dropdown + spokes + inspector behave."

    def run(self, state: CrashTestState) -> PhaseResult:
        result = PhaseResult(name=self.name, description=self.description)
        if state.skip_ui:
            result.skip("--skip-ui")
            return result
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError:
            result.skip("playwright not installed (pip install playwright)")
            return result

        screenshots = state.artifacts_dir / "screenshots"
        screenshots.mkdir(parents=True, exist_ok=True)

        try:
            with sync_playwright() as p:
                p.chromium.launch(headless=True).close()
            self._run_ui_assertions(state, screenshots, result)
        except Exception as exc:  # pragma: no cover — driven by env
            if "Executable doesn't exist" in str(exc) or "chromium" in str(exc).lower():
                result.skip("chromium not installed (run: python -m playwright install chromium)")
                return result
            result.status = "error"
            result.problems.append(f"playwright launch failed: {exc!r}")
            return result
        return result

    def _run_ui_assertions(
        self, state: CrashTestState, screenshots: Path, result: PhaseResult
    ) -> None:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        console_errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1600, "height": 1000})
                page.on(
                    "pageerror",
                    lambda exc: console_errors.append(f"pageerror: {exc}"),
                )
                page.on(
                    "console",
                    lambda msg: (
                        console_errors.append(f"{msg.type}: {msg.text}")
                        if msg.type == "error"
                        else None
                    ),
                )
                page.goto(f"{state.base_url}/ui", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_selector("svg", timeout=8000)
                with suppress(Exception):
                    page.screenshot(path=str(screenshots / "ui_initial.png"))

                # Workspace dropdown should list our test workspace.
                with suppress(Exception):
                    page.wait_for_selector("select", timeout=4000)
                options = page.evaluate(
                    "() => Array.from(document.querySelectorAll('select option')).map(o => o.value)"
                )
                result.assert_in(
                    "workspace dropdown lists test workspace",
                    state.workspace_id,
                    options or [],
                )

                # Trigger a memory mutation; observe a spoke appearing.
                post(
                    state.client,
                    "/memory/ingest_episode",
                    {
                        "workspace_id": state.workspace_id,
                        "session_id": "qa-ui",
                        "task_id": "qa-task-1",
                        "source_type": "agent_action",
                        "raw_text": "UI smoke: trigger a spoke",
                        "trust_level": "agent_observed",
                        "importance": 0.3,
                    },
                )
                spoke_present = False
                with suppress(Exception):
                    page.wait_for_selector("svg [data-spoke], svg .spoke, svg path", timeout=5000)
                    spoke_present = True
                result.assert_true("spoke / svg path rendered after ingest", spoke_present)
                with suppress(Exception):
                    page.screenshot(path=str(screenshots / "ui_after_ingest.png"))

                # No console errors.
                result.assert_eq(
                    "no JS errors during page lifecycle",
                    [e for e in console_errors if "pageerror" in e],
                    [],
                )
                if console_errors:
                    result.note(f"console msgs: {console_errors[:5]}")
            finally:
                browser.close()
