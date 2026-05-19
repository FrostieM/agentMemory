"""Lock the MCP local-fallback path for ``memory_write_decision`` and
``memory_write_theory``.

When the MCP stdio handler can't reach the HTTP service it falls back to
running the writer in-process against the runtime's connection. That
fallback used to return a bare 4-field dict — silently losing Move 1
(auto-thread ``source_episode_id``) and Move 3/4 (``capability_suggestions``)
on MCP-only deployments. This test pins the post-fix shape so the
regression can't come back.

The in-process ``tools_decisions.memory_write_decision`` and
``tools_theories.memory_write_theory`` (consumed via
``mcp.tools.dispatch``) share the same write helpers, so they get
the same shape via the same proof here.
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
from agent_memory_lite.mcp.tools_decisions import memory_write_decision
from agent_memory_lite.mcp.tools_theories import memory_write_theory


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


def _seed_episode_audit(conn: sqlite3.Connection, *, workspace_id: str, episode_id: str) -> None:
    """Seed both the audit row (so auto_thread finds it) AND a real
    episodes row (so the FK on decisions/theories.source_episode_id
    holds). Either alone leaves the test in a half-built state."""
    when = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES (?, ?, 'agent_action', 'fixture episode', ?)",
        (episode_id, workspace_id, when),
    )
    conn.execute(
        "INSERT INTO audit_log (id, workspace_id, action, target_type, target_id, "
        "created_at, agent_id) VALUES (?, ?, 'ingest_episode', 'episode', ?, ?, ?)",
        (f"a-{episode_id}", workspace_id, episode_id, when, "claude-test"),
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


# ---------- memory_write_decision (in-process MCP) ----------


def test_decision_local_fallback_returns_source_episode_id_and_suggestions(
    db: sqlite3.Connection,
) -> None:
    """The post-Move-1+3 contract: the in-process handler must populate
    ``source_episode_id`` from the recent audit row AND the
    ``capability_suggestions`` field from a matching workspace skill."""
    set_current_agent_id("claude-test")
    _seed_episode_audit(db, workspace_id="alpha", episode_id="ep_recent")
    _seed_skill(
        db,
        workspace_id="alpha",
        name="JWT migration",
        summary="auth refactor session cookies",
    )
    response = memory_write_decision(
        conn=db,
        payload={
            "workspace_id": "alpha",
            "title": "Move auth to JWT",
            "decision_text": "Replace session cookies with JWT tokens.",
            "rationale": "Auth refactor for cross-origin pages.",
        },
    )
    assert response["source_episode_id"] == "ep_recent"
    suggestions = response["capability_suggestions"]
    assert len(suggestions) == 1
    assert suggestions[0]["capability_name"] == "JWT migration"
    assert suggestions[0]["capability_type"] == "skill"
    # Move 5 contract: decision_neighbors present (empty when workspace
    # has no token-overlapping decisions, list of dicts otherwise).
    assert isinstance(response["decision_neighbors"], list)


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    decision_id: str,
    title: str,
    decision_text: str,
) -> None:
    now = "2026-05-19T00:00:00+00:00"
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale, status,
            supersedes_decision_id, source_episode_id, confidence,
            importance, valid_from, valid_to, created_at, updated_at,
            pinned)
           VALUES (?, ?, ?, ?, NULL, 'active', NULL, NULL, 0.9, 0.8,
                   ?, NULL, ?, ?, 0)""",
        (decision_id, workspace_id, title, decision_text, now, now, now),
    )
    conn.commit()


def test_decision_local_fallback_surfaces_decision_neighbors(
    db: sqlite3.Connection,
) -> None:
    """Move 5 (in-process MCP): existing token-overlapping decisions are
    surfaced via the ``decision_neighbors`` field on the write response,
    and the just-written decision excludes itself."""
    set_current_agent_id("claude-test")
    _seed_decision(
        db,
        workspace_id="alpha",
        decision_id="dec_existing",
        title="Kelly sizing for prediction markets",
        decision_text="Use quarter-Kelly fraction; multi-leg discount handled separately.",
    )
    response = memory_write_decision(
        conn=db,
        payload={
            "workspace_id": "alpha",
            "title": "Kelly bet-sizing variant",
            "decision_text": "Adopt quarter-Kelly fraction tweak for sizing.",
            "allow_orphan": True,
        },
    )
    neighbors = response["decision_neighbors"]
    assert isinstance(neighbors, list)
    assert len(neighbors) >= 1
    ids = {n["decision_id"] for n in neighbors}
    assert "dec_existing" in ids
    # Self-exclusion: the freshly-written id must NOT appear.
    assert response["decision_id"] not in ids


def test_decision_local_fallback_respects_allow_orphan(db: sqlite3.Connection) -> None:
    """allow_orphan=True must keep source_episode_id NULL even when a
    fresh audit row exists for the agent."""
    set_current_agent_id("claude-test")
    _seed_episode_audit(db, workspace_id="alpha", episode_id="ep_recent")
    response = memory_write_decision(
        conn=db,
        payload={
            "workspace_id": "alpha",
            "title": "Pre-existing decision",
            "decision_text": "This decision predates any episode recording.",
            "allow_orphan": True,
        },
    )
    assert response["source_episode_id"] is None


# ---------- memory_write_theory (in-process MCP) ----------


def test_theory_local_fallback_returns_source_episode_id_and_suggestions(
    db: sqlite3.Connection,
) -> None:
    """Same contract as decisions for theories — Move 1 + Move 4."""
    set_current_agent_id("claude-test")
    _seed_episode_audit(db, workspace_id="alpha", episode_id="ep_recent")
    _seed_skill(
        db,
        workspace_id="alpha",
        name="Source-flip replay",
        summary="tennis source-flip replay cohort design",
    )
    response = memory_write_theory(
        conn=db,
        payload={
            "workspace_id": "alpha",
            "title": "Source-flip tennis favorites",
            "domain": "trading.paper.edge",
            "claim": "Source-flip trades on tennis favorites may carry positive edge.",
            "mechanism": "Source wallet reacts before public odds adjust.",
        },
    )
    assert response["source_episode_id"] == "ep_recent"
    suggestions = response["capability_suggestions"]
    assert len(suggestions) == 1
    assert suggestions[0]["capability_name"] == "Source-flip replay"
    assert suggestions[0]["capability_type"] == "skill"


def test_theory_local_fallback_respects_allow_orphan(db: sqlite3.Connection) -> None:
    set_current_agent_id("claude-test")
    _seed_episode_audit(db, workspace_id="alpha", episode_id="ep_recent")
    response = memory_write_theory(
        conn=db,
        payload={
            "workspace_id": "alpha",
            "title": "Orphan theory",
            "domain": "general",
            "claim": "Predates any episode recording.",
            "allow_orphan": True,
        },
    )
    assert response["source_episode_id"] is None
