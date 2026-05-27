"""Audit 1 #2 fix: hub-mode cross-workspace writes are logged + audited.

Pre-fix the hub-mode bypass let a write to any workspace_id succeed
without trace — operator had no visibility into "the anchor process
just mutated another project's DB". This module locks the new
behavior: warn-once log + best-effort audit_log row into the anchor.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.mcp import stdio_guards
from agent_memory_lite.mcp.stdio_guards import (
    _audit_hub_cross_workspace_write,
    _ensure_workspace_writable,
    reset_hub_write_warned,
)
from agent_memory_lite.mcp.stdio_runtime import _runtime


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_hub_write_warned()
    monkeypatch.delenv("MEMORY_HUB_WRITE_AUDIT_ENABLED", raising=False)
    # Stub the runtime's anchor DB to an in-memory conn so insert_audit
    # has somewhere to land + we can inspect the result.
    stub_conn = sqlite3.connect(":memory:")
    stub_conn.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(stub_conn)
    monkeypatch.setattr(_runtime, "db", lambda: stub_conn)
    relaxed = _runtime.settings.model_copy(
        update={
            "workspace_id": "anchor-alpha",
            "hub_mode": True,
            "strict_workspace_isolation": False,
            "forbid_default_workspace": False,
        }
    )
    monkeypatch.setattr(_runtime, "settings", relaxed)
    try:
        yield
    finally:
        reset_hub_write_warned()
        stub_conn.close()


def test_anchor_write_does_not_audit(caplog: pytest.LogCaptureFixture) -> None:
    """Writing to the anchor's own workspace_id is normal, no audit row."""
    with caplog.at_level("WARNING", logger="agent_memory_lite.mcp.stdio_guards"):
        _ensure_workspace_writable("anchor-alpha")
    assert not any("cross-workspace" in r.getMessage() for r in caplog.records)
    rows = (
        _runtime.db()
        .execute("SELECT COUNT(*) FROM audit_log WHERE action = 'hub_cross_workspace_write'")
        .fetchone()
    )
    assert rows[0] == 0


def test_cross_workspace_write_warns_and_audits(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """In hub mode, write to foreign workspace_id → WARN log + audit row."""
    with caplog.at_level("WARNING", logger="agent_memory_lite.mcp.stdio_guards"):
        _ensure_workspace_writable("foreign-beta")
    assert any("cross-workspace" in r.getMessage() for r in caplog.records)
    rows = (
        _runtime.db()
        .execute(
            "SELECT workspace_id, target_id FROM audit_log "
            "WHERE action = 'hub_cross_workspace_write'"
        )
        .fetchall()
    )
    assert len(rows) == 1
    assert rows[0]["workspace_id"] == "anchor-alpha"
    assert rows[0]["target_id"] == "foreign-beta"


def test_cross_workspace_warn_is_once_per_pair(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Same (anchor, foreign) pair logged only once per process."""
    with caplog.at_level("WARNING", logger="agent_memory_lite.mcp.stdio_guards"):
        _ensure_workspace_writable("foreign-beta")
        _ensure_workspace_writable("foreign-beta")
        _ensure_workspace_writable("foreign-beta")
    warns = [r for r in caplog.records if "cross-workspace" in r.getMessage()]
    assert len(warns) == 1


def test_distinct_foreign_pairs_each_warn(caplog: pytest.LogCaptureFixture) -> None:
    """Different foreign workspaces each get their own first warning."""
    with caplog.at_level("WARNING", logger="agent_memory_lite.mcp.stdio_guards"):
        _ensure_workspace_writable("foreign-beta")
        _ensure_workspace_writable("foreign-gamma")
    warns = [r for r in caplog.records if "cross-workspace" in r.getMessage()]
    assert len(warns) == 2


def test_env_flag_disables_audit(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``MEMORY_HUB_WRITE_AUDIT_ENABLED=false`` suppresses both warn + row."""
    monkeypatch.setenv("MEMORY_HUB_WRITE_AUDIT_ENABLED", "false")
    with caplog.at_level("WARNING", logger="agent_memory_lite.mcp.stdio_guards"):
        _ensure_workspace_writable("foreign-beta")
    assert not any("cross-workspace" in r.getMessage() for r in caplog.records)
    rows = (
        _runtime.db()
        .execute("SELECT COUNT(*) FROM audit_log WHERE action = 'hub_cross_workspace_write'")
        .fetchone()
    )
    assert rows[0] == 0


def test_audit_function_is_failure_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    """If insert_audit raises (e.g. schema drift), the guard still succeeds."""
    from agent_memory_lite.repositories import audit_repo  # noqa: PLC0415

    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("audit subsystem broken")

    monkeypatch.setattr(audit_repo, "insert_audit", explode)
    # Should NOT raise — audit is informational.
    _audit_hub_cross_workspace_write("foreign-beta")


def test_strict_mode_still_blocks_cross_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strict isolation OFF hub-mode: existing block still fires."""
    strict = _runtime.settings.model_copy(
        update={"hub_mode": False, "strict_workspace_isolation": True}
    )
    monkeypatch.setattr(_runtime, "settings", strict)
    with pytest.raises(ValueError, match="blocked by MEMORY_STRICT_WORKSPACE_ISOLATION"):
        _ensure_workspace_writable("foreign-beta")


def test_reset_helper_clears_warned_set() -> None:
    """The test reset helper actually clears the set."""
    _audit_hub_cross_workspace_write("foreign-beta")
    assert ("anchor-alpha", "foreign-beta") in stdio_guards._hub_write_warned
    reset_hub_write_warned()
    assert ("anchor-alpha", "foreign-beta") not in stdio_guards._hub_write_warned
