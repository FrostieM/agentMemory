"""Sector 4 Round-2 adversarial audit — regression locks.

A fresh adversarial agent audited the API & MCP layer:
  CRITICAL — canonical HTTP write routes (/memory/write|edit|pin|
             archive|rollback) dropped the ensure_workspace_writable
             guard their legacy predecessors carried
  CRITICAL — the MCP v2-compat shim routed write-class tools to
             storage.writer with no workspace-isolation guard
  HIGH     — agent-supplied payload / fields keys were interpolated
             straight into SQL column lists (injection + workspace
             override)
  LOW      — canonical GET reads skipped ensure_workspace_readable

A follow-up re-audit found one deeper bug behind CRITICAL #2:
  RE-AUDIT CRITICAL — stdio_server._maybe_compat_dispatch wrapped the
             shim call in ``except Exception: return None``, so the
             v2-compat workspace guard's ValueError was swallowed and
             the call fell THROUGH to the native v2 handler — re-opening
             the cross-workspace write hole. The guard now runs before
             the try/except and returns an explicit block envelope.

This file locks every fix so a re-audit finds nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.config.settings import Settings

# storage.writer (versions snapshot) needs the canonical schema — the
# main apply_migrations path does not create the ``versions`` table.
_CANON = Path(__file__).resolve().parents[3] / "migrations" / "canonical"
_SCHEMA = _CANON / "0001_init.sql"
_OUTCOME = _CANON / "0002_outcome_loop.sql"


def _strict_settings() -> Settings:
    """Strict-isolation settings with anchor workspace 'ws-anchor'.

    Settings fields carry ``validation_alias`` — pydantic-settings only
    accepts the MEMORY_* alias as a constructor key, not the field name,
    so the plain-name kwargs must use the aliases here.
    """
    return Settings(
        MEMORY_STRICT_WORKSPACE_ISOLATION=True,  # type: ignore[call-arg]
        MEMORY_HUB_MODE=False,
        MEMORY_WORKSPACE_ID="ws-anchor",
        MEMORY_FORBID_DEFAULT_WORKSPACE=False,
    )


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
    conn.executescript(_OUTCOME.read_text(encoding="utf-8"))
    try:
        yield conn
    finally:
        conn.close()


# ---------- CRITICAL: canonical HTTP write routes enforce isolation ----------


def test_write_endpoint_blocks_foreign_workspace(db: sqlite3.Connection) -> None:
    """POST /memory/write into a foreign workspace under strict mode
    must be rejected — the route now re-checks ensure_workspace_writable."""
    from agent_memory_lite.api.errors import ValidationError  # noqa: PLC0415
    from agent_memory_lite.api.routes.memory import write_endpoint  # noqa: PLC0415
    from agent_memory_lite.api.schemas.memory import WriteRequest  # noqa: PLC0415

    req = WriteRequest(
        workspace_id="ws-victim",
        kind="decision",
        payload={"title": "pwn", "decision_text": "cross-workspace write"},
    )
    with pytest.raises(ValidationError, match="STRICT_WORKSPACE_ISOLATION"):
        write_endpoint(req, db, _strict_settings())


def test_edit_pin_archive_rollback_block_foreign_workspace(db: sqlite3.Connection) -> None:
    """The other 4 canonical write routes carry the same guard."""
    from agent_memory_lite.api.errors import ValidationError  # noqa: PLC0415
    from agent_memory_lite.api.routes.memory import (  # noqa: PLC0415
        archive_endpoint,
        edit_endpoint,
        pin_endpoint,
        rollback_endpoint,
    )
    from agent_memory_lite.api.schemas.memory import (  # noqa: PLC0415
        ArchiveRequest,
        EditRequest,
        PinRequest,
        RollbackRequest,
    )

    s = _strict_settings()
    with pytest.raises(ValidationError, match="STRICT_WORKSPACE_ISOLATION"):
        edit_endpoint(
            EditRequest(workspace_id="ws-victim", kind="decision", id="d1", fields={"title": "x"}),
            db,
            s,
        )
    with pytest.raises(ValidationError, match="STRICT_WORKSPACE_ISOLATION"):
        pin_endpoint(
            PinRequest(workspace_id="ws-victim", kind="decision", id="d1", pinned=True), db, s
        )
    with pytest.raises(ValidationError, match="STRICT_WORKSPACE_ISOLATION"):
        archive_endpoint(ArchiveRequest(workspace_id="ws-victim", kind="decision", id="d1"), db, s)
    with pytest.raises(ValidationError, match="STRICT_WORKSPACE_ISOLATION"):
        rollback_endpoint(
            RollbackRequest(
                workspace_id="ws-victim", kind="decision", id="d1", to_version=1, why="x"
            ),
            db,
            s,
        )


def test_write_endpoint_allows_anchor_workspace(db: sqlite3.Connection) -> None:
    """Sanity: a write to the anchor workspace still passes the guard."""
    from agent_memory_lite.api.routes.memory import write_endpoint  # noqa: PLC0415
    from agent_memory_lite.api.schemas.memory import WriteRequest  # noqa: PLC0415

    req = WriteRequest(
        workspace_id="ws-anchor",
        kind="decision",
        payload={"title": "legit", "decision_text": "anchor write"},
    )
    env = write_endpoint(req, db, _strict_settings())
    assert env.ok is True


# ---------- CRITICAL: MCP v2-compat shim enforces isolation ----------


def test_compat_dispatch_blocks_foreign_workspace_write(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """compat_dispatch must re-guard write-class shims. A v2
    memory_write_decision targeting a foreign workspace under strict
    mode must raise rather than reach storage.writer."""
    from agent_memory_lite.mcp import v2_compat  # noqa: PLC0415
    from agent_memory_lite.mcp.stdio_runtime import _runtime  # noqa: PLC0415

    monkeypatch.setattr(_runtime, "settings", _strict_settings())
    with pytest.raises(ValueError, match="STRICT_WORKSPACE_ISOLATION"):
        v2_compat.compat_dispatch(
            db,
            "memory_write_decision",
            {"workspace_id": "ws-victim", "title": "pwn", "decision_text": "x"},
        )


def test_compat_dispatch_read_tool_not_workspace_guarded(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-class shim (search) is NOT workspace-write-guarded —
    cross-workspace reads are intentionally allowed."""
    from agent_memory_lite.mcp import v2_compat  # noqa: PLC0415
    from agent_memory_lite.mcp.stdio_runtime import _runtime  # noqa: PLC0415

    monkeypatch.setattr(_runtime, "settings", _strict_settings())
    # Must not raise on a foreign workspace_id — returns an envelope.
    out = v2_compat.compat_dispatch(
        db, "memory_search", {"workspace_id": "ws-other", "query": "anything"}
    )
    assert out is not None


# ---------- HIGH: payload / fields keys whitelisted before SQL ----------


def test_writer_drops_unknown_payload_keys(db: sqlite3.Connection) -> None:
    """A payload key that is not a real column must be dropped, not
    interpolated into the INSERT column list."""
    from agent_memory_lite.storage.writer import write  # noqa: PLC0415

    out = write(
        db,
        workspace_id="ws",
        kind="decision",
        payload={
            "title": "real",
            "decision_text": "body",
            "totally_not_a_column": "junk",
        },
    )
    assert out is not None  # write succeeded; the junk key was dropped


def test_writer_payload_cannot_override_workspace(db: sqlite3.Connection) -> None:
    """payload={"workspace_id": "other"} must NOT retarget the row —
    the function arg is the source of truth."""
    from agent_memory_lite.storage.writer import write  # noqa: PLC0415

    out = write(
        db,
        workspace_id="ws-real",
        kind="decision",
        payload={"title": "t", "decision_text": "b", "workspace_id": "ws-hijack"},
    )
    assert out is not None
    row = db.execute("SELECT workspace_id FROM decisions WHERE id = ?", (out["id"],)).fetchone()
    assert row[0] == "ws-real", "payload overrode the row's workspace_id"


def test_writer_rejects_sql_injection_payload_key(db: sqlite3.Connection) -> None:
    """A SQL-injection-shaped payload key must be dropped — the
    decisions table must survive intact."""
    from agent_memory_lite.storage.writer import write  # noqa: PLC0415

    write(
        db,
        workspace_id="ws",
        kind="decision",
        payload={
            "title": "t",
            "decision_text": "b",
            "id) VALUES ('x'); DROP TABLE decisions;--": "boom",
        },
    )
    # The table still exists and is queryable.
    n = db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert n == 1


def test_edit_drops_unknown_field_keys(db: sqlite3.Connection) -> None:
    """edit() whitelists fields keys the same way — an unknown key is
    dropped; if nothing valid remains it is a no-op (returns None)."""
    from agent_memory_lite.storage.writer import edit, write  # noqa: PLC0415

    created = write(
        db, workspace_id="ws", kind="decision", payload={"title": "t", "decision_text": "b"}
    )
    assert created is not None
    # An edit with ONLY an unknown key → no valid columns → None.
    out = edit(
        db,
        workspace_id="ws",
        kind="decision",
        object_id=created["id"],
        fields={"not_a_real_column": "junk"},
    )
    assert out is None
    # A mixed edit keeps the valid key, drops the junk one.
    out2 = edit(
        db,
        workspace_id="ws",
        kind="decision",
        object_id=created["id"],
        fields={"title": "updated", "not_a_real_column": "junk"},
    )
    assert out2 is not None


# ---------- RE-AUDIT CRITICAL: the dispatch shim must surface the block ----------
#
# CRITICAL #2 added _ensure_workspace_writable inside the v2-compat shim,
# but stdio_server._maybe_compat_dispatch wrapped the shim call in
# ``except Exception: return None`` ("the shim must never raise"). A
# blocked write raised ValueError -> caught -> returned None -> the call
# fell through to the separately-registered native v2 handler, re-opening
# the cross-workspace write hole. The guard now runs BEFORE that
# try/except and returns an explicit error envelope.


def test_maybe_compat_dispatch_blocks_foreign_write_with_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked foreign-workspace v2-compat write must return an explicit
    error ENVELOPE from _maybe_compat_dispatch — never None. None would
    fall the call through to the native v2 handler."""
    from agent_memory_lite.mcp import stdio_server  # noqa: PLC0415
    from agent_memory_lite.mcp.stdio_runtime import _runtime  # noqa: PLC0415

    monkeypatch.setattr(stdio_server, "v2_compat_enabled", lambda: True)
    monkeypatch.setattr(_runtime, "settings", _strict_settings())

    result = stdio_server._maybe_compat_dispatch(
        "memory_write_decision",
        {"workspace_id": "ws-victim", "title": "pwn", "decision_text": "x"},
    )
    assert result is not None, "blocked write fell through to the native handler"
    assert result["ok"] is False
    assert result["error"]["code"] == "workspace_isolation_blocked"
    assert "STRICT_WORKSPACE_ISOLATION" in result["error"]["message"]


def test_maybe_compat_dispatch_allows_anchor_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: the guard does not false-positive — an anchor-workspace
    write proceeds to compat_dispatch instead of returning the block."""
    from agent_memory_lite.mcp import stdio_server  # noqa: PLC0415
    from agent_memory_lite.mcp.stdio_runtime import _runtime  # noqa: PLC0415

    monkeypatch.setattr(stdio_server, "v2_compat_enabled", lambda: True)
    monkeypatch.setattr(_runtime, "settings", _strict_settings())
    sentinel: dict[str, object] = {"ok": True, "dispatched": "compat"}
    monkeypatch.setattr(stdio_server, "compat_dispatch", lambda *_a: sentinel)
    monkeypatch.setattr(_runtime, "db_for", lambda *_a: object())

    result = stdio_server._maybe_compat_dispatch(
        "memory_write_decision",
        {"workspace_id": "ws-anchor", "title": "legit", "decision_text": "x"},
    )
    assert result == sentinel
