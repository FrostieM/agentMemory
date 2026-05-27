"""Sticky-brief: subsequent calls in the same session shrink max_tokens."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.cognition import brief as brief_mod
from agent_memory_lite.cognition.brief import compose_brief
from agent_memory_lite.utils.time import iso_now


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Reset per-process sticky state + brief cache between tests."""
    brief_mod._BRIEF_CACHE.clear()
    brief_mod.reset_session_seen()
    monkeypatch.delenv("MEMORY_STICKY_BRIEF_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS", raising=False)
    yield
    brief_mod._BRIEF_CACHE.clear()
    brief_mod.reset_session_seen()


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


def _seed_decision(conn: sqlite3.Connection, *, id_: str, title: str) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, pinned, importance, confidence)
           VALUES (?, 'ws', ?, ?, ?, 'active', ?, ?, ?, 1, 0.9, 0.9)""",
        (id_, title, f"Body of {title}", f"gist {title}", iso_now(), iso_now(), iso_now()),
    )
    conn.commit()


def test_first_call_full_budget(conn: sqlite3.Connection) -> None:
    """First call for a (workspace, session) pair gets the full budget."""
    for i in range(8):
        _seed_decision(conn, id_=f"dec_{i}", title=f"Pinned decision {i}")
    brief = compose_brief(conn, workspace_id="ws", max_tokens=500, session_id="sess-A")
    # Body should reflect full 500-budget composition.
    assert brief.token_count > 0


def test_followup_shrinks_to_floor(conn: sqlite3.Connection) -> None:
    """Second call in the same session is capped to the followup floor."""
    for i in range(8):
        _seed_decision(conn, id_=f"dec_{i}", title=f"Pinned decision {i}")
    first = compose_brief(conn, workspace_id="ws", max_tokens=500, session_id="sess-A")
    second = compose_brief(conn, workspace_id="ws", max_tokens=500, session_id="sess-A")
    # Default sticky floor = 200. Second brief must not exceed it.
    assert second.token_count <= 200
    assert second.token_count <= first.token_count


def test_session_isolation(conn: sqlite3.Connection) -> None:
    """A different session_id gets its own first-emit full budget."""
    for i in range(8):
        _seed_decision(conn, id_=f"dec_{i}", title=f"Pinned decision {i}")
    a = compose_brief(conn, workspace_id="ws", max_tokens=500, session_id="sess-A")
    b = compose_brief(conn, workspace_id="ws", max_tokens=500, session_id="sess-B")
    # Both first calls should hit the same full body (cached by fingerprint).
    assert a.body_md == b.body_md


def test_legacy_callers_without_session_id_skip_stickiness(conn: sqlite3.Connection) -> None:
    """Callers that don't pass session_id keep the old non-sticky behavior."""
    for i in range(8):
        _seed_decision(conn, id_=f"dec_{i}", title=f"Pinned decision {i}")
    first = compose_brief(conn, workspace_id="ws", max_tokens=500)
    second = compose_brief(conn, workspace_id="ws", max_tokens=500)
    # No session_id → no shrink. Bodies identical (cache hit on the second).
    assert first.body_md == second.body_md


def test_env_flag_disables_stickiness(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEMORY_STICKY_BRIEF_ENABLED=false restores full-budget behavior."""
    monkeypatch.setenv("MEMORY_STICKY_BRIEF_ENABLED", "false")
    for i in range(8):
        _seed_decision(conn, id_=f"dec_{i}", title=f"Pinned decision {i}")
    first = compose_brief(conn, workspace_id="ws", max_tokens=500, session_id="sess-A")
    second = compose_brief(conn, workspace_id="ws", max_tokens=500, session_id="sess-A")
    assert first.body_md == second.body_md


def test_followup_floor_env_override(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator can lift the floor via MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS."""
    monkeypatch.setenv("MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS", "350")
    for i in range(8):
        _seed_decision(conn, id_=f"dec_{i}", title=f"Pinned decision {i}")
    compose_brief(conn, workspace_id="ws", max_tokens=500, session_id="sess-A")
    second = compose_brief(conn, workspace_id="ws", max_tokens=500, session_id="sess-A")
    # Cap was lifted to 350 — second call should be allowed up to that.
    assert second.token_count <= 350


def test_followup_floor_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    """The floor reader clamps below-100 inputs to 100."""
    monkeypatch.setenv("MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS", "50")
    assert brief_mod._sticky_followup_tokens() == 100


def test_followup_does_not_exceed_caller_max_tokens(conn: sqlite3.Connection) -> None:
    """When caller asks for <floor (e.g. 150), sticky cap never inflates."""
    for i in range(4):
        _seed_decision(conn, id_=f"dec_{i}", title=f"Pinned decision {i}")
    compose_brief(conn, workspace_id="ws", max_tokens=150, session_id="sess-A")
    second = compose_brief(conn, workspace_id="ws", max_tokens=150, session_id="sess-A")
    # Caller's 150 is the upper bound — sticky never raises max_tokens.
    assert second.token_count <= 150


def test_reset_session_seen_helper() -> None:
    """The test helper must actually clear the per-process state."""
    brief_mod._session_seen.add(("ws", "sess-1"))
    assert ("ws", "sess-1") in brief_mod._session_seen
    brief_mod.reset_session_seen()
    assert ("ws", "sess-1") not in brief_mod._session_seen
