"""End-to-end crash test runner — exercises every memory feature against
a real HTTP service in an isolated workspace.

Architecture:
    scripts.crash_test.__main__   - CLI entry point
    scripts.crash_test.runner     - phase orchestrator
    scripts.crash_test.reporter   - PASS/FAIL/SKIP collector
    scripts.crash_test.workspace  - tmp workspace lifecycle
    scripts.crash_test.http_service - subprocess management
    scripts.crash_test.seeds      - shared seed helpers
    scripts.crash_test.phases.*   - one module per feature group

Adding a new phase:
    1. Drop a new ``pNN_xxx.py`` next to the others.
    2. Subclass ``Phase`` from ``phases._base``.
    3. Register it in ``runner.PHASES``.
"""
