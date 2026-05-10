"""Tests for the auto-thread provenance helper (Move 1, v2.2).

Locks the lookup logic in isolation: the helper finds the most recent
ingest_episode audit row for the current agent within the window, falls
back to a tighter anonymous window when no agent_id is set, and returns
None when nothing matches.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_memory_lite.api.agent_context import (
    reset_current_agent_id,
    set_current_agent_id,
)
from agent_memory_lite.db.connection import open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.ingestion.auto_thread_provenance import (
    find_recent_episode_for_agent,
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "src.db"
    conn = open_connection(db_path)
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _clear_agent_id():
    reset_current_agent_id()
    yield
    reset_current_agent_id()


def _seed_audit(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    action: str,
    target_id: str | None,
    agent_id: str | None,
    minutes_ago: float,
) -> None:
    when = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    conn.execute(
        "INSERT INTO audit_log (id, workspace_id, action, target_type, target_id, "
        "created_at, agent_id) VALUES (?, ?, ?, 'episode', ?, ?, ?)",
        (f"a-{minutes_ago:.3f}-{agent_id}", workspace_id, action, target_id, when, agent_id),
    )
    conn.commit()


def test_returns_target_id_for_matching_agent_within_window(
    db: sqlite3.Connection,
) -> None:
    set_current_agent_id("claude")
    _seed_audit(
        db,
        workspace_id="alpha",
        action="ingest_episode",
        target_id="ep_recent",
        agent_id="claude",
        minutes_ago=2.0,
    )
    found = find_recent_episode_for_agent(db, workspace_id="alpha", window_minutes=10)
    assert found == "ep_recent"


def test_picks_most_recent_when_multiple_matches(db: sqlite3.Connection) -> None:
    set_current_agent_id("claude")
    _seed_audit(
        db,
        workspace_id="alpha",
        action="ingest_episode",
        target_id="ep_old",
        agent_id="claude",
        minutes_ago=5.0,
    )
    _seed_audit(
        db,
        workspace_id="alpha",
        action="ingest_episode",
        target_id="ep_newer",
        agent_id="claude",
        minutes_ago=1.0,
    )
    assert find_recent_episode_for_agent(db, workspace_id="alpha") == "ep_newer"


def test_ignores_other_agent(db: sqlite3.Connection) -> None:
    set_current_agent_id("claude")
    _seed_audit(
        db,
        workspace_id="alpha",
        action="ingest_episode",
        target_id="ep_codex",
        agent_id="codex",
        minutes_ago=1.0,
    )
    assert find_recent_episode_for_agent(db, workspace_id="alpha") is None


def test_ignores_other_workspace(db: sqlite3.Connection) -> None:
    set_current_agent_id("claude")
    _seed_audit(
        db,
        workspace_id="beta",
        action="ingest_episode",
        target_id="ep_beta",
        agent_id="claude",
        minutes_ago=1.0,
    )
    assert find_recent_episode_for_agent(db, workspace_id="alpha") is None


def test_drops_outside_window(db: sqlite3.Connection) -> None:
    set_current_agent_id("claude")
    _seed_audit(
        db,
        workspace_id="alpha",
        action="ingest_episode",
        target_id="ep_old",
        agent_id="claude",
        minutes_ago=120.0,
    )
    assert find_recent_episode_for_agent(db, workspace_id="alpha", window_minutes=10) is None


def test_anonymous_fallback_uses_tight_window(db: sqlite3.Connection) -> None:
    """Without an agent_id, the helper falls back to a 60s window."""
    # No set_current_agent_id call; defaults to None.
    _seed_audit(
        db,
        workspace_id="alpha",
        action="ingest_episode",
        target_id="ep_recent",
        agent_id=None,
        minutes_ago=0.5,
    )
    assert find_recent_episode_for_agent(db, workspace_id="alpha") == "ep_recent"


def test_anonymous_fallback_drops_old_rows(db: sqlite3.Connection) -> None:
    """Anonymous lookup respects the 60s ceiling — older rows ignored."""
    _seed_audit(
        db,
        workspace_id="alpha",
        action="ingest_episode",
        target_id="ep_old",
        agent_id=None,
        minutes_ago=5.0,
    )
    assert find_recent_episode_for_agent(db, workspace_id="alpha") is None


def test_returns_none_when_audit_empty(db: sqlite3.Connection) -> None:
    set_current_agent_id("claude")
    assert find_recent_episode_for_agent(db, workspace_id="alpha") is None


def test_only_matches_ingest_episode_action(db: sqlite3.Connection) -> None:
    """Other actions (write_decision etc.) must not be picked up — the
    helper specifically wants the most recent EPISODE write."""
    set_current_agent_id("claude")
    _seed_audit(
        db,
        workspace_id="alpha",
        action="write_decision",
        target_id="dec_x",
        agent_id="claude",
        minutes_ago=1.0,
    )
    assert find_recent_episode_for_agent(db, workspace_id="alpha") is None
