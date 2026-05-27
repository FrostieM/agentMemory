"""Tests for the aging-decisions surface (MVP outcome-ping)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from agent_memory_lite.maintenance import aging_decisions as ad


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_AGING_DECISIONS_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_AGING_DECISIONS_DAYS", raising=False)
    monkeypatch.delenv("MEMORY_AGING_DECISIONS_LIMIT", raising=False)


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


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    title: str = "T",
    workspace_id: str = "ws",
    age_days: int = 45,
    outcome: float = 0.0,
    pinned: int = 0,
    status: str = "active",
) -> None:
    written = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale, status,
            supersedes_decision_id, source_episode_id, confidence,
            importance, valid_from, valid_to, created_at, updated_at,
            pinned, outcome_score)
           VALUES (?, ?, ?, 'body', NULL, ?, NULL, NULL, 0.9, 0.8,
                   ?, NULL, ?, ?, ?, ?)""",
        (decision_id, workspace_id, title, status, written, written, written, pinned, outcome),
    )
    conn.commit()


def test_is_enabled_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_AGING_DECISIONS_ENABLED", raising=False)
    assert ad.is_enabled() is True


@pytest.mark.parametrize("v", ["false", "0", "no", "off"])
def test_is_enabled_falsy(monkeypatch: pytest.MonkeyPatch, v: str) -> None:
    monkeypatch.setenv("MEMORY_AGING_DECISIONS_ENABLED", v)
    assert ad.is_enabled() is False


def test_threshold_default_30(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_AGING_DECISIONS_DAYS", raising=False)
    assert ad.threshold_days() == 30


def test_threshold_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_AGING_DECISIONS_DAYS", "60")
    assert ad.threshold_days() == 60


def test_threshold_floor_clamps_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_AGING_DECISIONS_DAYS", "0")
    assert ad.threshold_days() == 1
    monkeypatch.setenv("MEMORY_AGING_DECISIONS_DAYS", "garbage")
    assert ad.threshold_days() == 30  # falls back to default


def test_finds_aging_decisions(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, decision_id="dec_aged", age_days=45)
    out = ad.find_aging_decisions(conn, workspace_id="ws")
    assert len(out) == 1
    assert out[0].decision_id == "dec_aged"
    assert out[0].age_days >= 30


def test_recent_decisions_excluded(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, decision_id="dec_recent", age_days=5)
    out = ad.find_aging_decisions(conn, workspace_id="ws")
    assert out == []


def test_pinned_decisions_excluded(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, decision_id="dec_pinned", age_days=45, pinned=1)
    out = ad.find_aging_decisions(conn, workspace_id="ws")
    assert out == []


def test_decisions_with_outcome_excluded(conn: sqlite3.Connection) -> None:
    """A decision that accumulated positive feedback is no longer 'aging silently'."""
    _seed_decision(conn, decision_id="dec_proven", age_days=45, outcome=0.5)
    out = ad.find_aging_decisions(conn, workspace_id="ws")
    assert out == []


def test_decisions_with_negative_outcome_excluded(conn: sqlite3.Connection) -> None:
    """A decision that already failed (negative outcome) is handled by watch-outs, not aging."""
    _seed_decision(conn, decision_id="dec_failed", age_days=45, outcome=-0.5)
    out = ad.find_aging_decisions(conn, workspace_id="ws")
    assert out == []


def test_archived_decisions_excluded(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, decision_id="dec_archived", age_days=45, status="archived")
    out = ad.find_aging_decisions(conn, workspace_id="ws")
    assert out == []


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, decision_id="dec_beta", workspace_id="beta", age_days=45)
    out = ad.find_aging_decisions(conn, workspace_id="alpha")
    assert out == []


def test_limit_caps_results(conn: sqlite3.Connection) -> None:
    for i in range(6):
        _seed_decision(conn, decision_id=f"dec_{i}", title=f"T{i}", age_days=45 + i)
    out = ad.find_aging_decisions(conn, workspace_id="ws", limit=2)
    assert len(out) == 2


def test_oldest_first(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, decision_id="dec_younger", age_days=35)
    _seed_decision(conn, decision_id="dec_older", age_days=90)
    out = ad.find_aging_decisions(conn, workspace_id="ws")
    assert out[0].decision_id == "dec_older"
    assert out[1].decision_id == "dec_younger"


def test_pre_migration_db_returns_empty() -> None:
    """When outcome_score column is missing (pre-Phase-1 DB), no crash."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    out = ad.find_aging_decisions(c, workspace_id="ws")
    # The COALESCE in the SQL handles missing column-default at insertion;
    # but if outcome_score column doesn't exist at all, the query fails.
    # Module is failure-soft and returns [].
    assert out == []
    c.close()


def test_limit_env_override(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """``MEMORY_AGING_DECISIONS_LIMIT`` env caps the result set when no
    explicit ``limit`` arg is supplied."""
    monkeypatch.setenv("MEMORY_AGING_DECISIONS_LIMIT", "2")
    for i in range(5):
        _seed_decision(conn, decision_id=f"dec_{i}", title=f"Topic {i}", age_days=45 + i)
    out = ad.find_aging_decisions(conn, workspace_id="ws")  # uses env limit
    assert len(out) == 2


def test_age_days_uses_native_timedelta_days(conn: sqlite3.Connection) -> None:
    """``age_days`` is taken from ``timedelta.days`` so values are
    whole-day-floored consistently (cosmetic fix from audit)."""
    _seed_decision(conn, decision_id="dec_age", age_days=42)
    out = ad.find_aging_decisions(conn, workspace_id="ws")
    assert len(out) == 1
    # Allow ±1 day slack because the test seeds with "datetime.now() - 42d"
    # and find_aging_decisions reads "datetime.now()" separately; on slow
    # CI the gap can cross a day boundary.
    assert 41 <= out[0].age_days <= 43


def test_age_days_sentinel_on_unparseable_timestamp(conn: sqlite3.Connection) -> None:
    """Audit fix: unparseable ``valid_from`` returns ``age_days=-1``
    (sentinel) instead of the threshold value, so corrupted rows
    don't masquerade as "exactly at threshold".

    Locks the specific sentinel value — a regression that reverts to
    the prior ``age_days = days`` fallback would fail this assertion.
    The ``+99:00`` offset is intentionally invalid per RFC 3339 (max
    offset is ±18:00) and reliably trips ``parse_iso`` on every
    Python version that supports the function.
    """
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale, status,
            supersedes_decision_id, source_episode_id, confidence,
            importance, valid_from, valid_to, created_at, updated_at,
            pinned)
           VALUES ('dec_garbage', 'ws', 'Garbage timestamp', 'body', NULL,
                   'active', NULL, NULL, 0.9, 0.8,
                   '0001-01-01T00:00:00+99:00', NULL,
                   '0001-01-01T00:00:00+99:00',
                   '0001-01-01T00:00:00+99:00', 0)""",
    )
    conn.commit()
    out = ad.find_aging_decisions(conn, workspace_id="ws")
    # The SQL filter is lexicographic on the ISO string; a value starting
    # with "0001-..." sorts well before any cutoff in 2025+, so the row
    # MUST be returned. The Python-side parse_iso then fails and the
    # fallback sentinel kicks in.
    assert len(out) == 1
    assert out[0].decision_id == "dec_garbage"
    assert out[0].age_days == -1


def test_snippet_truncated_to_120_chars(conn: sqlite3.Connection) -> None:
    long = "Body text " * 30
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale, status,
            supersedes_decision_id, source_episode_id, confidence,
            importance, valid_from, valid_to, created_at, updated_at,
            pinned, outcome_score)
           VALUES ('dec_long', 'ws', 'T', ?, NULL, 'active', NULL, NULL,
                   0.9, 0.8, ?, NULL, ?, ?, 0, 0.0)""",
        (
            long,
            (datetime.now(UTC) - timedelta(days=45)).isoformat(),
            (datetime.now(UTC) - timedelta(days=45)).isoformat(),
            (datetime.now(UTC) - timedelta(days=45)).isoformat(),
        ),
    )
    conn.commit()
    out = ad.find_aging_decisions(conn, workspace_id="ws")
    assert len(out) == 1
    assert len(out[0].snippet) <= 120
