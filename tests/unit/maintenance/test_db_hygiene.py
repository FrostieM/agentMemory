"""Periodic SQLite hygiene: WAL checkpoint every tick + occasional VACUUM."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_memory_lite.config import workspace_meta
from agent_memory_lite.db.connection import open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.maintenance import db_hygiene
from agent_memory_lite.maintenance.db_hygiene import (
    _LAST_VACUUM_KEY,
    is_enabled,
    run_db_hygiene,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_DB_HYGIENE_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_VACUUM_INTERVAL_HOURS", raising=False)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_path = tmp_path / "src.db"
    c = open_connection(db_path)
    apply_migrations(c)
    yield c
    c.close()


def test_is_enabled_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_DB_HYGIENE_ENABLED", raising=False)
    assert is_enabled() is True


@pytest.mark.parametrize("v", ["false", "0", "no", "off"])
def test_is_enabled_falsy(monkeypatch: pytest.MonkeyPatch, v: str) -> None:
    monkeypatch.setenv("MEMORY_DB_HYGIENE_ENABLED", v)
    assert is_enabled() is False


def test_disabled_short_circuits(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """When disabled, returns sentinel report with -1 page count + no errors."""
    monkeypatch.setenv("MEMORY_DB_HYGIENE_ENABLED", "false")
    report = run_db_hygiene(conn, workspace_id="ws")
    assert report.wal_checkpoint_pages == -1
    assert report.vacuum_ran is False
    assert not report.errors


def test_wal_checkpoint_runs_on_every_call(conn: sqlite3.Connection) -> None:
    """WAL checkpoint is the cheap path; always runs when enabled."""
    report = run_db_hygiene(conn, workspace_id="ws")
    # On a fresh DB the checkpoint count is 0 (nothing in WAL) but the
    # call MUST have happened (no error).
    assert not report.errors
    assert report.wal_checkpoint_pages >= 0


def test_vacuum_runs_when_no_prior_record(conn: sqlite3.Connection) -> None:
    """No ``last_vacuum_at`` → VACUUM is overdue → runs."""
    report = run_db_hygiene(conn, workspace_id="ws")
    assert report.vacuum_ran is True
    # The stamp lands so the next call won't VACUUM again.
    stamp = workspace_meta.get(conn, "ws", _LAST_VACUUM_KEY)
    assert stamp is not None


def test_vacuum_skipped_when_recent(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recent ``last_vacuum_at`` → VACUUM is NOT overdue → skipped."""
    # Stamp last vacuum at 1h ago, interval at 24h → not overdue.
    monkeypatch.setenv("MEMORY_VACUUM_INTERVAL_HOURS", "24")
    one_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    workspace_meta.set_value(conn, "ws", _LAST_VACUUM_KEY, one_hour_ago)
    conn.commit()
    report = run_db_hygiene(conn, workspace_id="ws")
    assert report.vacuum_ran is False
    assert report.wal_checkpoint_pages >= 0


def test_vacuum_runs_when_stamp_is_stale(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stamp older than interval → VACUUM fires again."""
    monkeypatch.setenv("MEMORY_VACUUM_INTERVAL_HOURS", "24")
    stale = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    workspace_meta.set_value(conn, "ws", _LAST_VACUUM_KEY, stale)
    conn.commit()
    report = run_db_hygiene(conn, workspace_id="ws")
    assert report.vacuum_ran is True


def test_vacuum_overdue_on_unparseable_stamp(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrupt stamp value → treat as overdue (safer than skipping)."""
    monkeypatch.setenv("MEMORY_VACUUM_INTERVAL_HOURS", "24")
    workspace_meta.set_value(conn, "ws", _LAST_VACUUM_KEY, "GARBAGE")
    conn.commit()
    report = run_db_hygiene(conn, workspace_id="ws")
    assert report.vacuum_ran is True


def test_interval_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """The interval env var parses correctly + clamps floor to 1.0."""
    monkeypatch.setenv("MEMORY_VACUUM_INTERVAL_HOURS", "72")
    assert db_hygiene._interval_hours() == 72.0
    monkeypatch.setenv("MEMORY_VACUUM_INTERVAL_HOURS", "0")
    assert db_hygiene._interval_hours() == 1.0  # clamped
    monkeypatch.setenv("MEMORY_VACUUM_INTERVAL_HOURS", "garbage")
    assert db_hygiene._interval_hours() == 168.0  # fallback


# ============================================================
# Audit-round-2 C#1 — SQLITE_BUSY cooldown
# ============================================================


def test_busy_classifier_picks_locked(conn: sqlite3.Connection) -> None:
    """``_is_busy_error`` distinguishes contention from data errors."""
    busy = sqlite3.OperationalError("database is locked")
    other = sqlite3.OperationalError("syntax error near 'foo'")
    integrity = sqlite3.IntegrityError("UNIQUE constraint failed")
    assert db_hygiene._is_busy_error(busy) is True
    assert db_hygiene._is_busy_error(other) is False
    assert db_hygiene._is_busy_error(integrity) is False


class _ConnProxy:
    """Wrapper that intercepts execute() to inject a SQLITE_BUSY for
    specific SQL patterns. Used because sqlite3.Connection's execute
    attribute is read-only and can't be monkeypatched directly."""

    def __init__(self, real: sqlite3.Connection, fail_pattern: str) -> None:
        self._real = real
        self._fail = fail_pattern

    def execute(self, sql: str, *args: object, **kwargs: object) -> object:
        if self._fail in sql:
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *args, **kwargs)

    def commit(self) -> None:
        self._real.commit()


def test_vacuum_busy_records_cooldown(conn: sqlite3.Connection) -> None:
    """When VACUUM raises sqlite3.OperationalError('locked'), the
    report tags it as vacuum_busy AND a cooldown deadline lands in
    workspace_meta so the next tick skips."""
    proxy = _ConnProxy(conn, fail_pattern="VACUUM")
    report = run_db_hygiene(proxy, workspace_id="ws")  # type: ignore[arg-type]
    assert report.vacuum_ran is False
    assert any("vacuum_busy" in e for e in (report.errors or []))
    # Cooldown deadline now in workspace_meta.
    cooldown = workspace_meta.get(conn, "ws", db_hygiene._VACUUM_BUSY_COOLDOWN_KEY)
    assert cooldown


def test_vacuum_skipped_during_busy_cooldown(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cooldown deadline in the future → VACUUM is NOT attempted."""
    future = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    workspace_meta.set_value(conn, "ws", db_hygiene._VACUUM_BUSY_COOLDOWN_KEY, future)
    conn.commit()
    # Force vacuum_overdue to True so the only thing skipping is the cooldown.
    workspace_meta.set_value(conn, "ws", db_hygiene._LAST_VACUUM_KEY, "")
    conn.commit()
    report = run_db_hygiene(conn, workspace_id="ws")
    assert report.vacuum_ran is False
    # No vacuum_busy error this time — we never even tried.
    assert not any("vacuum_busy" in e for e in (report.errors or []))


def test_wal_checkpoint_busy_tagged_distinctly(conn: sqlite3.Connection) -> None:
    """SQLITE_BUSY on the checkpoint surfaces as ``wal_checkpoint_busy``."""
    proxy = _ConnProxy(conn, fail_pattern="wal_checkpoint")
    report = run_db_hygiene(proxy, workspace_id="ws")  # type: ignore[arg-type]
    assert any("wal_checkpoint_busy" in e for e in (report.errors or []))
