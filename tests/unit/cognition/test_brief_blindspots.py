"""Brief surfaces a `## Blindspots` section when episodes mention
tokens never covered by decisions."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_memory_lite.cognition import brief as brief_mod
from agent_memory_lite.cognition.brief import compose_brief

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    brief_mod._BRIEF_CACHE.clear()
    brief_mod.reset_session_seen()
    monkeypatch.delenv("MEMORY_BLINDSPOT_DETECT_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_BLINDSPOT_MIN_EPISODES", raising=False)
    monkeypatch.setenv("MEMORY_BLINDSPOT_MIN_EPISODES", "3")
    yield
    brief_mod._BRIEF_CACHE.clear()
    brief_mod.reset_session_seen()


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
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


def test_brief_surfaces_blindspots(conn: sqlite3.Connection) -> None:
    for i in range(3):
        _seed_episode(conn, idx=i, text="kelly sizing analysis")
    body = compose_brief(conn, workspace_id="ws", max_tokens=500).body_md
    assert "## Blindspots" in body
    # At least one of the three tokens lands as a blindspot row. The
    # 0.03 budget slot may only fit one row at default ``max_tokens``;
    # which token wins depends on Counter's insertion order for ties.
    expected_tokens = {"kelly", "sizing", "analysis"}
    assert any(f"'{tok}'" in body for tok in expected_tokens), body


def test_brief_skips_blindspots_when_empty(conn: sqlite3.Connection) -> None:
    body = compose_brief(conn, workspace_id="ws", max_tokens=500).body_md
    assert "Blindspots" not in body


def test_brief_skips_blindspots_when_disabled(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_BLINDSPOT_DETECT_ENABLED", "false")
    for i in range(3):
        _seed_episode(conn, idx=i, text="kelly sizing")
    body = compose_brief(conn, workspace_id="ws", max_tokens=500).body_md
    assert "Blindspots" not in body
