"""Brief surfaces an `## Aging decisions` section for old zero-feedback rows."""

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
    monkeypatch.delenv("MEMORY_AGING_DECISIONS_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_AGING_DECISIONS_DAYS", raising=False)
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


def _seed_decision(conn: sqlite3.Connection, *, decision_id: str, age_days: int) -> None:
    written = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale, status,
            supersedes_decision_id, source_episode_id, confidence,
            importance, valid_from, valid_to, created_at, updated_at,
            pinned, outcome_score)
           VALUES (?, 'ws', ?, 'body', NULL, 'active', NULL, NULL,
                   0.9, 0.8, ?, NULL, ?, ?, 0, 0.0)""",
        (decision_id, f"Aging dec {decision_id}", written, written, written),
    )
    conn.commit()


def test_brief_surfaces_aging_decisions(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, decision_id="dec_aged_1", age_days=45)
    _seed_decision(conn, decision_id="dec_aged_2", age_days=60)
    brief = compose_brief(conn, workspace_id="ws", max_tokens=500)
    assert "Aging decisions" in brief.body_md
    assert "dec_aged_1" in brief.body_md or "dec_aged_2" in brief.body_md


def test_brief_skips_aging_section_when_no_rows(conn: sqlite3.Connection) -> None:
    """Empty section emits no header — no noise when nothing's aging."""
    brief = compose_brief(conn, workspace_id="ws", max_tokens=500)
    assert "Aging decisions" not in brief.body_md


def test_brief_skips_aging_section_when_disabled(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_AGING_DECISIONS_ENABLED", "false")
    _seed_decision(conn, decision_id="dec_aged", age_days=45)
    brief = compose_brief(conn, workspace_id="ws", max_tokens=500)
    assert "Aging decisions" not in brief.body_md


def test_brief_aging_respects_threshold_env(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raising the threshold above the row's age hides it from the Aging section.

    The row still shows in the regular Active-decisions section (it IS
    an active decision); the assertion narrows to the Aging section.
    """
    _seed_decision(conn, decision_id="dec_30d", age_days=35)
    monkeypatch.setenv("MEMORY_AGING_DECISIONS_DAYS", "60")
    brief = compose_brief(conn, workspace_id="ws", max_tokens=500)
    assert "## Aging decisions" not in brief.body_md
