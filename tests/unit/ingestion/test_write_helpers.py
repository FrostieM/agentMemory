"""Unit tests for ``ingestion/_write_helpers.py``.

The helpers are shared by the HTTP routes and the MCP fallback / in-process
tool handlers, so locking their behaviour here prevents Move 1 + Move 3/4
silently regressing on any of those surfaces.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_memory_lite.api.agent_context import reset_current_agent_id, set_current_agent_id
from agent_memory_lite.db.connection import open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.ingestion._write_helpers import (
    capability_suggestion_dicts,
    resolve_source_episode_id,
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_connection(tmp_path / "src.db")
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _clear_agent_id():
    reset_current_agent_id()
    yield
    reset_current_agent_id()


def _settings(*, auto_thread: bool = True) -> SimpleNamespace:
    """Minimal stand-in for ``Settings`` — only the field the helper reads."""
    return SimpleNamespace(auto_thread_decision_source=auto_thread)


def _seed_episode_audit(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    target_id: str,
    agent_id: str | None,
    minutes_ago: float,
) -> None:
    when = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    conn.execute(
        "INSERT INTO audit_log (id, workspace_id, action, target_type, target_id, "
        "created_at, agent_id) VALUES (?, ?, 'ingest_episode', 'episode', ?, ?, ?)",
        (f"a-{minutes_ago:.3f}-{agent_id}", workspace_id, target_id, when, agent_id),
    )
    conn.commit()


def _seed_skill(conn: sqlite3.Connection, *, workspace_id: str, name: str, summary: str) -> None:
    now = "2026-05-10T00:00:00+00:00"
    conn.execute(
        """INSERT INTO agent_skills
           (id, workspace_id, name, summary, when_to_use_json, inputs_json, outputs_json,
            tools_json, related_roles_json, source_episode_id, confidence, active,
            created_at, updated_at, usage_count, success_count, failure_count, last_invoked_at)
           VALUES (?, ?, ?, ?, '[]', '[]', '[]', '[]', '[]', NULL, 0.9, 1,
                   ?, ?, 0, 0, 0, NULL)""",
        (f"sk-{name}", workspace_id, name, summary, now, now),
    )
    conn.commit()


# ---------- resolve_source_episode_id ----------


def test_resolve_returns_explicit_value_unchanged(db: sqlite3.Connection) -> None:
    """When the caller passes an explicit id, the helper returns it verbatim
    and never even consults the audit log."""
    out = resolve_source_episode_id(
        db,
        workspace_id="alpha",
        explicit="ep_explicit",
        allow_orphan=False,
        settings=_settings(auto_thread=True),
    )
    assert out == "ep_explicit"


def test_resolve_returns_none_when_allow_orphan(db: sqlite3.Connection) -> None:
    """allow_orphan=True is the deliberate untraced-write opt-out."""
    set_current_agent_id("claude-test")
    _seed_episode_audit(
        db, workspace_id="alpha", target_id="ep_recent", agent_id="claude-test", minutes_ago=1.0
    )
    out = resolve_source_episode_id(
        db,
        workspace_id="alpha",
        explicit=None,
        allow_orphan=True,
        settings=_settings(auto_thread=True),
    )
    assert out is None


def test_resolve_returns_none_when_setting_off(db: sqlite3.Connection) -> None:
    """auto_thread_decision_source=False short-circuits the lookup."""
    set_current_agent_id("claude-test")
    _seed_episode_audit(
        db, workspace_id="alpha", target_id="ep_recent", agent_id="claude-test", minutes_ago=1.0
    )
    out = resolve_source_episode_id(
        db,
        workspace_id="alpha",
        explicit=None,
        allow_orphan=False,
        settings=_settings(auto_thread=False),
    )
    assert out is None


def test_resolve_finds_recent_episode_for_agent(db: sqlite3.Connection) -> None:
    """When auto_thread is on and a fresh ingest_episode audit row exists,
    the helper returns its target_id."""
    set_current_agent_id("claude-test")
    _seed_episode_audit(
        db, workspace_id="alpha", target_id="ep_recent", agent_id="claude-test", minutes_ago=1.0
    )
    out = resolve_source_episode_id(
        db,
        workspace_id="alpha",
        explicit=None,
        allow_orphan=False,
        settings=_settings(auto_thread=True),
    )
    assert out == "ep_recent"


# ---------- capability_suggestion_dicts ----------


def test_capability_suggestion_dicts_returns_wire_shape(db: sqlite3.Connection) -> None:
    """The dict-shape returns the same keys the HTTP wire payload uses."""
    _seed_skill(
        db,
        workspace_id="alpha",
        name="JWT migration",
        summary="auth refactor session cookies replacement",
    )
    out = capability_suggestion_dicts(
        db,
        workspace_id="alpha",
        title="Move auth to JWT",
        text="replace session cookies with JWT",
        rationale="auth refactor",
    )
    assert len(out) == 1
    item = out[0]
    assert set(item) >= {
        "capability_type",
        "capability_id",
        "capability_name",
        "score",
        "snippet",
    }
    assert item["capability_name"] == "JWT migration"
    assert item["capability_type"] == "skill"


def test_capability_suggestion_dicts_empty_when_no_match(db: sqlite3.Connection) -> None:
    """An empty list (not None) when nothing in the workspace matches."""
    out = capability_suggestion_dicts(db, workspace_id="alpha", title="x", text="y", rationale="z")
    assert out == []
