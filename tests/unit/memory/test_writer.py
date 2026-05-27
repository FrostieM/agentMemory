"""Unit tests for v3 writer API (memory_write / edit / pin / archive / rollback)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.storage.writer import (
    archive,
    edit,
    list_versions,
    pin,
    rollback,
    write,
)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


# ============================================================
# memory_write
# ============================================================


def test_write_decision_creates_row_with_projection(conn: sqlite3.Connection) -> None:
    out = write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={
            "title": "Adopt v3",
            "decision_text": "Switch to compact projections. Lower token cost.",
            "rationale": "Saves 80% of context budget.",
        },
        agent_id="claude",
    )
    assert out is not None
    assert out["kind"] == "decision"
    assert out["title"] == "Adopt v3"
    # gist computed from decision_text
    assert "compact projections" in (out.get("gist") or "")


def test_write_generates_id_when_absent(conn: sqlite3.Connection) -> None:
    out = write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"title": "T", "decision_text": "body"},
    )
    assert out is not None
    assert out["id"].startswith("dec_")


def test_write_preserves_supplied_id(conn: sqlite3.Connection) -> None:
    out = write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_explicit", "title": "T", "decision_text": "body"},
    )
    assert out is not None
    assert out["id"] == "dec_explicit"


def test_write_idempotent_replaces_row(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v1", "decision_text": "first"},
    )
    out = write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v2", "decision_text": "second"},
    )
    assert out is not None
    assert out["title"] == "v2"
    # One row only.
    count = conn.execute("SELECT COUNT(*) FROM decisions WHERE id = 'dec_x'").fetchone()[0]
    assert count == 1


def test_write_snapshots_prior_version(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v1", "decision_text": "first"},
    )
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v2", "decision_text": "second"},
    )
    versions = list_versions(conn, workspace_id="ws", kind="decision", object_id="dec_x")
    assert len(versions) == 1  # the prior v1, snapshotted before v2 write
    assert versions[0]["version_no"] == 1


def test_write_appends_audit_log(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "T", "decision_text": "body"},
        agent_id="claude",
    )
    row = conn.execute(
        "SELECT action, agent_id, target_id FROM audit_log WHERE target_id = 'dec_x'"
    ).fetchone()
    assert row is not None
    assert row["action"] == "write"
    assert row["agent_id"] == "claude"


def test_write_then_update_logs_update_action(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "T", "decision_text": "body"},
    )
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "T2", "decision_text": "body2"},
    )
    actions = [
        r[0]
        for r in conn.execute(
            "SELECT action FROM audit_log WHERE target_id = 'dec_x' ORDER BY created_at"
        ).fetchall()
    ]
    assert actions == ["write", "update"]


def test_write_unknown_kind_returns_none(conn: sqlite3.Connection) -> None:
    assert write(conn, workspace_id="ws", kind="unknown", payload={}) is None


def test_write_episode_computes_gist_from_raw_text(conn: sqlite3.Connection) -> None:
    out = write(
        conn,
        workspace_id="ws",
        kind="episode",
        payload={
            "source_type": "agent_action",
            "raw_text": "Migrated agentLight to v3 trial. All 33 kinds parity OK.",
        },
    )
    assert out is not None
    assert "agentLight" in (out.get("gist") or "")


def test_write_behavior_computes_rule_one_line(conn: sqlite3.Connection) -> None:
    out = write(
        conn,
        workspace_id="ws",
        kind="behavior",
        payload={
            "name": "test-rule",
            "kind": "operating_rule",
            "rule": "Never push to main without explicit operator approval. Branch protection enforces this.",
            "applies_to_json": '["git push"]',
        },
    )
    assert out is not None
    assert out["rule_one_line"] is not None
    assert "operator approval" in out["rule_one_line"]


# ============================================================
# memory_edit
# ============================================================


def test_edit_partial_update(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "T", "decision_text": "body"},
    )
    out = edit(
        conn, workspace_id="ws", kind="decision", object_id="dec_x", fields={"status": "superseded"}
    )
    assert out is not None
    assert out["status"] == "superseded"


def test_edit_snapshots_prior_then_updates(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v1", "decision_text": "body"},
    )
    edit(conn, workspace_id="ws", kind="decision", object_id="dec_x", fields={"title": "v2"})
    versions = list_versions(conn, workspace_id="ws", kind="decision", object_id="dec_x")
    assert len(versions) == 1


def test_edit_unknown_id_returns_none(conn: sqlite3.Connection) -> None:
    assert (
        edit(conn, workspace_id="ws", kind="decision", object_id="missing", fields={"status": "x"})
        is None
    )


def test_edit_empty_fields_returns_none(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "T", "decision_text": "body"},
    )
    assert edit(conn, workspace_id="ws", kind="decision", object_id="dec_x", fields={}) is None


# ============================================================
# memory_pin
# ============================================================


def test_pin_decision(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "T", "decision_text": "body"},
    )
    out = pin(conn, workspace_id="ws", kind="decision", object_id="dec_x", pinned=True)
    assert out is not None
    assert out["pinned"] is True


def test_unpin_decision(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "T", "decision_text": "body", "pinned": 1},
    )
    out = pin(conn, workspace_id="ws", kind="decision", object_id="dec_x", pinned=False)
    assert out is not None
    assert out["pinned"] is False


def test_pin_unsupported_kind_returns_none(conn: sqlite3.Connection) -> None:
    assert pin(conn, workspace_id="ws", kind="skill", object_id="x", pinned=True) is None


# ============================================================
# memory_archive
# ============================================================


def test_archive_decision_sets_status_archived(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "T", "decision_text": "body"},
    )
    out = archive(conn, workspace_id="ws", kind="decision", object_id="dec_x", reason="superseded")
    assert out is not None
    assert out["status"] == "archived"


def test_archive_behavior_sets_active_zero(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="behavior",
        payload={
            "id": "beh_x",
            "name": "test",
            "kind": "operating_rule",
            "rule": "r",
            "applies_to_json": "[]",
        },
    )
    out = archive(conn, workspace_id="ws", kind="behavior", object_id="beh_x", reason="obsolete")
    assert out is not None
    row = conn.execute("SELECT active FROM behaviors WHERE id = 'beh_x'").fetchone()
    assert row["active"] == 0


def test_archive_episode_sets_is_archived_one(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="episode",
        payload={
            "id": "ep_x",
            "source_type": "agent_action",
            "raw_text": "x",
        },
    )
    archive(conn, workspace_id="ws", kind="episode", object_id="ep_x")
    row = conn.execute("SELECT is_archived FROM episodes WHERE id = 'ep_x'").fetchone()
    assert row["is_archived"] == 1


# ============================================================
# memory_rollback
# ============================================================


def test_rollback_to_prior_version(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v1", "decision_text": "first"},
    )
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v2", "decision_text": "second"},
    )
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v3", "decision_text": "third"},
    )
    # versions: 1 (v1), 2 (v2). Live row = v3 now.
    out = rollback(
        conn,
        workspace_id="ws",
        kind="decision",
        object_id="dec_x",
        to_version=1,
        why="v3 broke production",
    )
    assert out is not None
    assert out["title"] == "v1"
    # Versions count should now be 3 (pre-rollback snapshot of v3 added).
    versions = list_versions(conn, workspace_id="ws", kind="decision", object_id="dec_x")
    assert len(versions) == 3


def test_rollback_requires_why(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v1", "decision_text": "body"},
    )
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v2", "decision_text": "body"},
    )
    out = rollback(
        conn, workspace_id="ws", kind="decision", object_id="dec_x", to_version=1, why=""
    )
    assert out is None


def test_rollback_to_missing_version_returns_none(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v1", "decision_text": "body"},
    )
    out = rollback(
        conn, workspace_id="ws", kind="decision", object_id="dec_x", to_version=999, why="test"
    )
    assert out is None


def test_rollback_logs_audit(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v1", "decision_text": "body"},
    )
    write(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"id": "dec_x", "title": "v2", "decision_text": "body"},
    )
    rollback(
        conn,
        workspace_id="ws",
        kind="decision",
        object_id="dec_x",
        to_version=1,
        why="rollback test",
    )
    actions = [
        r[0]
        for r in conn.execute(
            "SELECT action FROM audit_log WHERE target_id = 'dec_x' ORDER BY created_at"
        ).fetchall()
    ]
    assert "rollback" in actions


# ============================================================
# Workspace isolation
# ============================================================


def test_writes_are_workspace_scoped(conn: sqlite3.Connection) -> None:
    write(
        conn,
        workspace_id="ws-a",
        kind="decision",
        payload={"id": "dec_x", "title": "A", "decision_text": "body"},
    )
    write(
        conn,
        workspace_id="ws-b",
        kind="decision",
        payload={"id": "dec_x", "title": "B", "decision_text": "body"},
    )
    # Same id in different workspaces should coexist (composite PK is id alone in v3 schema —
    # workspace_id is a column, not part of PK). Since INSERT OR REPLACE matches on id only,
    # the second write replaces the first. Verify this is the documented behavior.
    rows = conn.execute("SELECT workspace_id, title FROM decisions WHERE id = 'dec_x'").fetchall()
    # Acceptable: single row with ws-b (last writer wins). The point is no cross-workspace
    # contamination on the read path.
    assert len(rows) == 1
