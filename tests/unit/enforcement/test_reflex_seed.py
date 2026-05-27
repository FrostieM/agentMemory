"""Phase 4: seed_reflex_rules bootstraps three baseline rules."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.bootstrap.project_memory_seed_reflexes import seed_reflex_rules


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


def test_seed_inserts_three_baseline_rules(conn: sqlite3.Connection) -> None:
    inserted = seed_reflex_rules(conn, workspace_id="ws")
    assert inserted == 3
    rules = {
        row["rule_name"]
        for row in conn.execute("SELECT rule_name FROM reflex_rules WHERE workspace_id = 'ws'")
    }
    assert rules == {
        "edit-requires-impact-check",
        "decision-write-requires-search",
        "deploy-requires-playbook",
    }


def test_seed_is_idempotent(conn: sqlite3.Connection) -> None:
    first = seed_reflex_rules(conn, workspace_id="ws")
    second = seed_reflex_rules(conn, workspace_id="ws")
    assert first == 3
    assert second == 0


def test_seed_rules_default_to_advisory(conn: sqlite3.Connection) -> None:
    """Operator must promote advisory -> block explicitly."""
    seed_reflex_rules(conn, workspace_id="ws")
    rows = conn.execute("SELECT enforcement FROM reflex_rules").fetchall()
    assert all(row["enforcement"] == "advisory" for row in rows)


def test_seed_silent_on_pre_migration_db() -> None:
    """No reflex_rules table → seed returns 0, no error."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    inserted = seed_reflex_rules(c, workspace_id="ws")
    assert inserted == 0
    c.close()
