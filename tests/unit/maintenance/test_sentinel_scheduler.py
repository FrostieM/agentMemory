"""Sentinel scheduler — overdue logic + flag gating + workspace_meta stamp.

The full sentinel run is hard to unit-test (needs embedding provider +
vector store + yaml file). This file pins the cheap parts: ``_is_overdue``,
``maybe_run_sentinels`` gating, and the workspace_meta last-run stamp.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_memory_lite.config import workspace_meta
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.config.workspace_registry import WorkspaceRegistry
from agent_memory_lite.maintenance import sentinel_lock, sentinel_scheduler


@pytest.fixture(autouse=True)
def _reset_locks() -> None:
    """Each test gets a clean per-workspace lock map; otherwise a held
    lock from a prior test would block the in-flight check spuriously."""
    sentinel_lock.reset_for_tests()


def _settings(*, autorun_hours: float, db_path: Path) -> Settings:
    """Settings whose anchor DB IS ``db_path`` and whose registry file is
    an (absent) tmp path — so the sentinel scheduler's cross-workspace
    guard sees a pass for the anchor workspace on ``db_path`` as
    correctly routed instead of aborting it."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_SENTINEL_AUTORUN_HOURS=str(autorun_hours),
        MEMORY_DB_PATH=str(db_path),
        MEMORY_WORKSPACES_FILE=str(db_path.parent / "workspaces.json"),
    )


def _stamp_last_run(conn: sqlite3.Connection, *, hours_ago: float) -> None:
    when = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    workspace_meta.set_value(conn, "default", "last_sentinel_run_at", when)


def test_overdue_when_no_prior_run(applied_conn: sqlite3.Connection, tmp_db_path: Path) -> None:
    # _is_overdue opens its own connection — point it at our applied_conn DB.
    overdue = sentinel_scheduler._is_overdue(
        db_path=tmp_db_path, workspace_id="default", threshold_hours=1.0
    )
    assert overdue is True


def test_not_overdue_when_just_ran(applied_conn: sqlite3.Connection, tmp_db_path: Path) -> None:
    _stamp_last_run(applied_conn, hours_ago=0.0)
    applied_conn.commit()
    overdue = sentinel_scheduler._is_overdue(
        db_path=tmp_db_path, workspace_id="default", threshold_hours=1.0
    )
    assert overdue is False


def test_overdue_when_run_was_long_ago(applied_conn: sqlite3.Connection, tmp_db_path: Path) -> None:
    _stamp_last_run(applied_conn, hours_ago=10.0)
    applied_conn.commit()
    overdue = sentinel_scheduler._is_overdue(
        db_path=tmp_db_path, workspace_id="default", threshold_hours=1.0
    )
    assert overdue is True


def test_overdue_handles_malformed_timestamp(
    applied_conn: sqlite3.Connection, tmp_db_path: Path
) -> None:
    workspace_meta.set_value(applied_conn, "default", "last_sentinel_run_at", "not-a-date")
    applied_conn.commit()
    overdue = sentinel_scheduler._is_overdue(
        db_path=tmp_db_path, workspace_id="default", threshold_hours=1.0
    )
    # Garbage in workspace_meta should be treated as "no prior run" → overdue.
    assert overdue is True


def test_disabled_when_threshold_zero(applied_conn: sqlite3.Connection, tmp_db_path: Path) -> None:
    settings = _settings(autorun_hours=0.0, db_path=tmp_db_path)
    scheduled = sentinel_scheduler.maybe_run_sentinels(
        workspace_id="default",
        settings=settings,
        db_path=tmp_db_path,
        embedding_provider=None,
        vector_store=None,
    )
    assert scheduled is False


def test_not_scheduled_when_recent_run(applied_conn: sqlite3.Connection, tmp_db_path: Path) -> None:
    _stamp_last_run(applied_conn, hours_ago=0.0)
    applied_conn.commit()
    settings = _settings(autorun_hours=24.0, db_path=tmp_db_path)
    scheduled = sentinel_scheduler.maybe_run_sentinels(
        workspace_id="default",
        settings=settings,
        db_path=tmp_db_path,
        embedding_provider=None,
        vector_store=None,
    )
    assert scheduled is False


def test_scheduled_when_overdue_no_yaml_stamps_last_run(
    applied_conn: sqlite3.Connection, tmp_db_path: Path
) -> None:
    """When overdue but no sentinels yaml exists, the scheduler still
    stamps last_run_at so we don't try again on the next request."""
    settings = _settings(autorun_hours=1.0, db_path=tmp_db_path)
    scheduled = sentinel_scheduler.maybe_run_sentinels(
        workspace_id="default",
        settings=settings,
        db_path=tmp_db_path,
        embedding_provider=None,
        vector_store=None,
    )
    assert scheduled is True

    # Wait for the daemon thread (no yaml → just stamps timestamp).
    deadline = time.time() + 5.0
    while time.time() < deadline:
        applied_conn.commit()  # checkpoint so we see the thread's writes
        last_run = workspace_meta.get(applied_conn, "default", "last_sentinel_run_at")
        if last_run:
            return
        time.sleep(0.1)
    raise AssertionError("scheduler thread did not stamp last_run_at within 5s")


def test_in_flight_lock_blocks_concurrent_run(
    applied_conn: sqlite3.Connection, tmp_db_path: Path
) -> None:
    """Two simultaneous get_context calls must not spawn two daemons.

    Pre-acquire the workspace's lock to simulate an in-flight run, then call
    maybe_run_sentinels and assert it bails out — without the lock fix,
    both calls would see "overdue" and start daemons in parallel.
    """
    # No prior stamp → would be "overdue".
    settings = _settings(autorun_hours=1.0, db_path=tmp_db_path)
    held = sentinel_lock.get_workspace_lock("default")
    assert held.acquire(blocking=False) is True
    try:
        scheduled = sentinel_scheduler.maybe_run_sentinels(
            workspace_id="default",
            settings=settings,
            db_path=tmp_db_path,
            embedding_provider=None,
            vector_store=None,
        )
        assert scheduled is False, (
            "second concurrent caller spawned a daemon despite in-flight lock"
        )
    finally:
        held.release()


def test_lock_released_after_run(applied_conn: sqlite3.Connection, tmp_db_path: Path) -> None:
    """After a normal pass completes, the next overdue tick must succeed.

    Schedules one run, waits for it to finish (lock release), then schedules
    another from a "long-ago" stamp and expects it to succeed.
    """
    settings = _settings(autorun_hours=1.0, db_path=tmp_db_path)
    sentinel_scheduler.maybe_run_sentinels(
        workspace_id="default",
        settings=settings,
        db_path=tmp_db_path,
        embedding_provider=None,
        vector_store=None,
    )
    # Wait for the lock to be released (daemon finishes).
    held = sentinel_lock.get_workspace_lock("default")
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if held.acquire(blocking=False):
            held.release()
            break
        time.sleep(0.1)
    else:
        raise AssertionError("lock was never released")

    # Reset so it looks long-ago overdue.
    _stamp_last_run(applied_conn, hours_ago=10.0)
    applied_conn.commit()
    second = sentinel_scheduler.maybe_run_sentinels(
        workspace_id="default",
        settings=settings,
        db_path=tmp_db_path,
        embedding_provider=None,
        vector_store=None,
    )
    assert second is True, "second overdue tick failed to schedule after release"


def test_run_sentinel_pass_aborts_on_mismatched_db(tmp_path: Path) -> None:
    """A misrouted (workspace_id, db_path) pair aborts before any write.

    Regression for the 2026-05-22 leak: a brain pass run on a connection
    that is not the workspace's registered DB wrote one workspace's
    self_model / workspace_meta rows into a foreign database.

    ``wrong.db`` carries the real schema, so with the guard reverted the
    pass WOULD reach ``workspace_meta.set_value`` and stamp ``ws-a``'s
    last-run row -- the assertion below is load-bearing only because of
    that.
    """
    schema = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
    workspaces_file = tmp_path / "workspaces.json"
    WorkspaceRegistry(workspaces_file).register(
        workspace_id="ws-a",
        db_path=str(tmp_path / "ws-a.db"),
        vector_path=str(tmp_path / "ws-a.lance"),
    )
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_WORKSPACES_FILE=str(workspaces_file),
    )
    wrong_db = tmp_path / "wrong.db"
    seed = sqlite3.connect(str(wrong_db))
    try:
        seed.executescript(schema.read_text(encoding="utf-8"))
        seed.commit()
    finally:
        seed.close()

    # workspace_id "ws-a" paired with a db_path that is NOT its registered DB.
    sentinel_scheduler._run_sentinel_pass(
        workspace_id="ws-a",
        db_path=wrong_db,
        settings=settings,
        embedding_provider=None,
        vector_store=None,
    )

    # The guard fired: the pass returned before any write, so the foreign
    # DB has zero ws-a workspace_meta rows. With the guard reverted the
    # pass would have stamped ws-a's last_sentinel_run_at here.
    conn = sqlite3.connect(str(wrong_db))
    try:
        ws_a_rows = conn.execute(
            "SELECT COUNT(*) FROM workspace_meta WHERE workspace_id = ?", ("ws-a",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert ws_a_rows == 0
