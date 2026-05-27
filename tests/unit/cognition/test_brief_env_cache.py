"""Brief cache invalidates when relevant env flags flip.

Audit 3 #2 fix: previously the cache key was
``(workspace_id, max_tokens, workspace_fingerprint)`` where fingerprint
only hashed DB-mutation timestamps. Flipping
``MEMORY_AGING_DECISIONS_DAYS`` (or any of the brief-composition env
flags) didn't invalidate the cache → required a process restart. The
fix folds an env-signature into the fingerprint so toggles take effect
immediately.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from agent_memory_lite.cognition import brief as brief_mod
from agent_memory_lite.cognition.brief import compose_brief


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    brief_mod._BRIEF_CACHE.clear()
    brief_mod.reset_session_seen()
    for key in brief_mod._BRIEF_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
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


def _seed_episode(conn: sqlite3.Connection, *, idx: int, text: str) -> None:
    ts = (datetime.now(UTC) - timedelta(hours=idx)).isoformat()
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES (?, 'ws', 'agent_action', ?, ?)",
        (f"ep_{idx}", text, ts),
    )
    conn.commit()


def test_env_signature_changes_on_flag_flip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Signature is purely functional of env state → flipping a flag
    must change the hash."""
    sig_a = brief_mod._brief_env_signature()
    monkeypatch.setenv("MEMORY_BLINDSPOT_DETECT_ENABLED", "false")
    sig_b = brief_mod._brief_env_signature()
    assert sig_a != sig_b


def test_cache_invalidates_when_blindspot_disabled(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First call with blindspots on, second with blindspots off — the
    second call must NOT return the cached body (it should reflect the
    new env state)."""
    monkeypatch.setenv("MEMORY_BLINDSPOT_MIN_EPISODES", "3")
    for i in range(3):
        _seed_episode(conn, idx=i, text="kelly sizing")
    body_on = compose_brief(conn, workspace_id="ws", max_tokens=500).body_md
    assert "Blindspots" in body_on
    monkeypatch.setenv("MEMORY_BLINDSPOT_DETECT_ENABLED", "false")
    body_off = compose_brief(conn, workspace_id="ws", max_tokens=500).body_md
    assert "Blindspots" not in body_off


def test_cache_key_differs_when_threshold_changes(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flipping any composition-affecting env must put the second call
    on a NEW cache key (cache_hit=False), regardless of whether the
    surface output changed.
    """
    first = compose_brief(conn, workspace_id="ws", max_tokens=500)
    assert first.cache_hit is False
    monkeypatch.setenv("MEMORY_AGING_DECISIONS_DAYS", "60")
    second = compose_brief(conn, workspace_id="ws", max_tokens=500)
    assert second.cache_hit is False  # cache key changed → fresh build


def test_cache_hits_when_env_unchanged(conn: sqlite3.Connection) -> None:
    """Two calls with identical env + identical DB state → second call
    must be a cache hit (``cache_hit=True``)."""
    first = compose_brief(conn, workspace_id="ws", max_tokens=500)
    second = compose_brief(conn, workspace_id="ws", max_tokens=500)
    assert second.cache_hit is True
    assert first.body_md == second.body_md
