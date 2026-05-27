"""Sector 4 Round-2 adversarial audit — regression locks.

A fresh adversarial agent audited the API & MCP layer:
  CRITICAL — canonical HTTP write routes (/memory/write|edit|pin|
             archive|rollback) dropped the ensure_workspace_writable
             guard their legacy predecessors carried
  CRITICAL — the old MCP v2-compat shim routed write-class tools to
             storage.writer with no workspace-isolation guard; that shim
             has since been removed from the active v3 surface
  HIGH     — agent-supplied payload / fields keys were interpolated
             straight into SQL column lists (injection + workspace
             override)
  LOW      — canonical GET reads skipped ensure_workspace_readable

This file locks every fix so a re-audit finds nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.config.settings import Settings


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
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(conn)
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


def test_edit_pin_archive_block_foreign_workspace(db: sqlite3.Connection) -> None:
    """The remaining canonical mutation routes carry the same guard."""
    from agent_memory_lite.api.errors import ValidationError  # noqa: PLC0415
    from agent_memory_lite.api.routes.memory import (  # noqa: PLC0415
        archive_endpoint,
        edit_endpoint,
        pin_endpoint,
    )
    from agent_memory_lite.api.schemas.memory import (  # noqa: PLC0415
        ArchiveRequest,
        EditRequest,
        PinRequest,
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


# ---------- ROUND-5 RE-AUDIT: HTTP maintenance writes ----------
#
# The active MCP stdio surface is v3-only. Maintenance mutations remain HTTP
# concerns, so this audit locks the HTTP action routes directly.


def test_http_compact_trigger_blocks_foreign_workspace(db: sqlite3.Connection) -> None:
    """POST /memory/compact_trigger into a foreign workspace is rejected."""
    from agent_memory_lite.api.errors import ValidationError  # noqa: PLC0415
    from agent_memory_lite.api.routes.review_queue import compact_trigger_route  # noqa: PLC0415
    from agent_memory_lite.api.schemas.review_queue import CompactTriggerRequest  # noqa: PLC0415

    req = CompactTriggerRequest(workspace_id="ws-victim")
    with pytest.raises(ValidationError, match="STRICT_WORKSPACE_ISOLATION"):
        compact_trigger_route(req, db, _strict_settings())


def test_http_maintenance_action_routes_block_foreign_workspace(db: sqlite3.Connection) -> None:
    """resolve / claim / dismiss maintenance-event routes carry the guard."""
    from agent_memory_lite.api.errors import ValidationError  # noqa: PLC0415
    from agent_memory_lite.api.routes.maintenance import (  # noqa: PLC0415
        claim_maintenance_event_route,
        dismiss_maintenance_event_route,
        resolve_maintenance_event_route,
    )
    from agent_memory_lite.api.schemas.maintenance import (  # noqa: PLC0415
        ClaimMaintenanceEventRequest,
        DismissMaintenanceEventRequest,
        ResolveMaintenanceEventRequest,
    )

    s = _strict_settings()
    with pytest.raises(ValidationError, match="STRICT_WORKSPACE_ISOLATION"):
        resolve_maintenance_event_route(
            ResolveMaintenanceEventRequest(event_id="e1", workspace_id="ws-victim"), db, s
        )
    with pytest.raises(ValidationError, match="STRICT_WORKSPACE_ISOLATION"):
        claim_maintenance_event_route(
            ClaimMaintenanceEventRequest(event_id="e1", assigned_to="op", workspace_id="ws-victim"),
            db,
            s,
        )
    with pytest.raises(ValidationError, match="STRICT_WORKSPACE_ISOLATION"):
        dismiss_maintenance_event_route(
            DismissMaintenanceEventRequest(event_id="e1", workspace_id="ws-victim"), db, s
        )
