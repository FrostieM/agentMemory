"""Lock the warn-once behavior of _runtime.db_for fallback.

Audit finding 2026-05-19 #4: silent fallback to anchor when the
requested workspace_id is not in the registry hides misconfiguration —
the operator gets wrong-empty results without any signal. The fix logs
ONCE per unresolved id so the bug surfaces in service logs without
spamming the log every call.

Tests cover:
 * first lookup of an unknown id emits a warning + records the id in
   the warn-once set;
 * second lookup of the same id is silent (no duplicate log line);
 * a different unknown id triggers its own first warning;
 * the warning is suppressed for the anchor workspace itself (a caller
   passing workspace_id == settings.workspace_id is by definition not
   misconfigured).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.mcp import stdio_runtime
from agent_memory_lite.mcp.stdio_runtime import _runtime, reset_unresolved_warned


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Reset the warn-once set AND stub out the anchor DB opener so the
    fallback path in ``db_for`` doesn't try to open the real anchor DB
    (which would fail on workspace_manifest mismatch in this fresh fixture).
    """
    reset_unresolved_warned()
    stub_conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(_runtime, "db", lambda: stub_conn)
    try:
        yield
    finally:
        reset_unresolved_warned()
        stub_conn.close()


def test_unknown_workspace_logs_warning_once(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First lookup of an unknown id warns; second is silent."""
    # Force registry lookup to return None so we hit the warn path.
    monkeypatch.setattr(stdio_runtime, "_registry_paths_for", lambda *a, **k: None)
    with caplog.at_level("WARNING", logger="agent_memory_lite.mcp.stdio_unresolved_warn"):
        _runtime.db_for("nonexistent-workspace")
        first_count = sum(1 for r in caplog.records if "db_for" in r.getMessage())
        _runtime.db_for("nonexistent-workspace")
        second_count = sum(1 for r in caplog.records if "db_for" in r.getMessage())
    assert first_count == 1
    assert second_count == 1  # second call did NOT add another warning


def test_distinct_unknown_workspaces_each_warn(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Different unresolved ids each get their first emit."""
    monkeypatch.setattr(stdio_runtime, "_registry_paths_for", lambda *a, **k: None)
    with caplog.at_level("WARNING", logger="agent_memory_lite.mcp.stdio_unresolved_warn"):
        _runtime.db_for("alpha-missing")
        _runtime.db_for("beta-missing")
    warnings = [r for r in caplog.records if "db_for" in r.getMessage()]
    assert len(warnings) == 2


def test_anchor_workspace_does_not_warn(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing the anchor workspace_id back to db_for is normal, not misconfig."""
    monkeypatch.setattr(stdio_runtime, "_registry_paths_for", lambda *a, **k: None)
    with caplog.at_level("WARNING", logger="agent_memory_lite.mcp.stdio_unresolved_warn"):
        _runtime.db_for(_runtime.settings.workspace_id)
    warnings = [r for r in caplog.records if "db_for" in r.getMessage()]
    assert warnings == []


def test_reset_helper_clears_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test reset helper must actually clear the warn-once set."""
    monkeypatch.setattr(stdio_runtime, "_registry_paths_for", lambda *a, **k: None)
    _runtime.db_for("alpha")
    assert "alpha" in stdio_runtime._unresolved_warned
    reset_unresolved_warned()
    assert "alpha" not in stdio_runtime._unresolved_warned
