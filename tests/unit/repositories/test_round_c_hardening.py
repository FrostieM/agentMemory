"""global-audit round-C: secret-redaction + concurrency hardening at the SQL layer."""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.fts.durable_fts import sync_durable_fts
from agent_memory_lite.repositories.soft_edges_repo import upsert_soft_edge
from agent_memory_lite.storage.writer import write


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_migrations(c)
    return c


def test_upsert_soft_edge_accumulates_weight_atomically(conn: sqlite3.Connection) -> None:
    """Round-C: the UPDATE-then-INSERT was a race that dropped a concurrent weight
    increment. The atomic INSERT .. ON CONFLICT DO UPDATE folds repeats into one row."""
    for _ in range(3):
        upsert_soft_edge(conn, workspace_id="default", src="a", dst="b", kind="co_retrieved")
    row = conn.execute(
        "SELECT weight, observation_count FROM soft_edges WHERE src_qualified_name = 'a'"
    ).fetchone()
    assert row["weight"] == pytest.approx(3.0)
    assert row["observation_count"] == 3
    # exactly one row exists (no duplicate inserts under the unique index)
    assert conn.execute("SELECT COUNT(*) FROM soft_edges").fetchone()[0] == 1


def test_write_redacts_secrets_defense_in_depth(conn: sqlite3.Connection) -> None:
    """Round-C: storage.writer.write() is a PUBLIC export; a direct call must not
    persist secrets in the row (write_canonical already redacts upstream)."""
    write(
        conn,
        workspace_id="default",
        kind="concept",
        payload={
            "name": "leaky",
            "definition": "DATABASE_URL=postgres://u:hunter2@h/db",
            "concept_kind": "metric",
        },
    )
    definition = conn.execute("SELECT definition FROM concepts").fetchone()["definition"]
    assert "hunter2" not in definition
    assert "REDACTED" in definition


def test_sync_durable_fts_delete_is_workspace_scoped(conn: sqlite3.Connection) -> None:
    """Round-C: the FTS DELETE is scoped to workspace_id, so a mis-passed workspace
    can never evict another workspace's index row."""
    # Seed a decision in ws1 + index it.
    write(
        conn,
        workspace_id="ws1",
        kind="decision",
        payload={"id": "dec_ws1", "title": "t", "decision_text": "FTS body about retries"},
    )
    sync_durable_fts(conn, kind="decision", object_id="dec_ws1", workspace_id="ws1")
    assert (
        conn.execute("SELECT COUNT(*) FROM durable_fts WHERE object_id = 'dec_ws1'").fetchone()[0]
        == 1
    )
    # A sync with the WRONG workspace must NOT delete ws1's index row.
    sync_durable_fts(conn, kind="decision", object_id="dec_ws1", workspace_id="ws2")
    assert (
        conn.execute("SELECT COUNT(*) FROM durable_fts WHERE object_id = 'dec_ws1'").fetchone()[0]
        == 1
    )
