"""v3.4 #7 — V4 method (b) Granger causality extractor tests.

Covers the deterministic lead-lag detector: builds per-day activity
vectors from ``memory_usage_feedback``, computes pairwise
``|corr(X_{t-1}, Y_t)| - |corr(Y_{t-1}, Y_t)|`` and emits
``causal_link(relation='granger_caused')`` when the gap clears the
threshold AND the lead-lag correlation is positive.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from agent_memory_lite.retrieval.causal_granger import (
    GrangerReport,
    _lead_lag_strength,
    extract_granger_links,
    is_enabled,
    threshold,
    window_days,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MEMORY_CAUSAL_GRANGER_ENABLED",
        "MEMORY_CAUSAL_GRANGER_WINDOW_DAYS",
        "MEMORY_CAUSAL_GRANGER_THRESHOLD",
        "MEMORY_CAUSAL_GRANGER_MIN_ACTIVITY",
        "MEMORY_CAUSAL_GRANGER_MAX_PAIRS",
    ):
        monkeypatch.delenv(name, raising=False)


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


def _record_usage(
    conn: sqlite3.Connection,
    *,
    src_type: str,
    src_id: str,
    days_ago: int,
    workspace_id: str = "ws",
    bucket_id: str | None = None,
) -> None:
    """Insert one memory_usage_feedback row dated N days ago.

    Each call has to use a unique ``id``; pass an explicit ``bucket_id``
    when seeding multiple uses in the same day to avoid the PRIMARY KEY
    collision."""
    when = datetime.now(UTC) - timedelta(days=days_ago)
    iso = when.isoformat()
    row_id = f"uf_{src_id}_{days_ago}" if bucket_id is None else bucket_id
    conn.execute(
        """INSERT INTO memory_usage_feedback
           (id, workspace_id, source_type, source_id, query, usefulness,
            task_id, notes, source, created_at)
           VALUES (?, ?, ?, ?, '', 0.5, NULL, '', 'agent_observed', ?)""",
        (row_id, workspace_id, src_type, src_id, iso),
    )
    conn.commit()


def _count_granger(conn: sqlite3.Connection, workspace_id: str = "ws") -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM causal_links WHERE workspace_id = ? AND relation = 'granger_caused'",
        (workspace_id,),
    ).fetchone()[0]


def test_defaults() -> None:
    assert is_enabled() is True
    assert threshold() == pytest.approx(0.25)
    assert window_days() == 30


def test_lead_lag_strength_positive_signal() -> None:
    """Y mirrors X with a one-day lag — strong positive lead-lag, weak
    self-correlation for Y → strength > 0."""
    # X spikes on days 0, 3, 6; Y spikes on days 1, 4, 7 — perfect lag-1.
    x = [1, 0, 0, 1, 0, 0, 1, 0]
    y = [0, 1, 0, 0, 1, 0, 0, 1]
    s = _lead_lag_strength(x, y)
    assert s > 0.3, f"expected strong positive Granger signal, got {s}"


def test_lead_lag_strength_negative_direction_zeroed() -> None:
    """X spikes AFTER Y → Y leads X, X does not lead Y → strength = 0
    in the X→Y direction. (The pair-loop in extract_granger_links
    checks both directions independently, so the Y→X direction would
    pick the signal up.)"""
    y = [1, 0, 0, 1, 0, 0, 1, 0]
    x = [0, 1, 0, 0, 1, 0, 0, 1]
    # X lagged should NOT predict Y better than Y itself does.
    s = _lead_lag_strength(x, y)
    assert s == 0.0


def test_lead_lag_strength_constant_series_returns_zero() -> None:
    """statistics.correlation raises on a constant series. The helper
    must catch that and return 0 instead of leaking the error."""
    assert _lead_lag_strength([0, 0, 0, 0, 0], [1, 0, 1, 0, 1]) == 0.0
    assert _lead_lag_strength([1, 0, 1, 0, 1], [0, 0, 0, 0, 0]) == 0.0


def test_extract_no_activity_returns_zero(conn: sqlite3.Connection) -> None:
    """Empty memory_usage_feedback → empty report; nothing to correlate."""
    result = extract_granger_links(conn, workspace_id="ws")
    assert result == GrangerReport(pairs_scanned=0, links_emitted=0)


def test_extract_strong_lead_lag_emits_link(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X (dec_lead) gets used on days 8, 5, 2; Y (dec_follow) on days
    7, 4, 1 — Y trails X by one day, perfect Granger pattern. Threshold
    relaxed slightly so the small-window noise does not block the signal."""
    monkeypatch.setenv("MEMORY_CAUSAL_GRANGER_THRESHOLD", "0.2")
    for days in (8, 5, 2):
        _record_usage(conn, src_type="decision", src_id="dec_lead", days_ago=days)
    for days in (7, 4, 1):
        _record_usage(conn, src_type="decision", src_id="dec_follow", days_ago=days)
    result = extract_granger_links(conn, workspace_id="ws")
    assert result.pairs_scanned >= 1
    assert result.links_emitted >= 1
    rows = conn.execute(
        "SELECT src_id, dst_id, weight FROM causal_links "
        "WHERE workspace_id = 'ws' AND relation = 'granger_caused'"
    ).fetchall()
    # The pair should be ordered lead → follow.
    assert any(r["src_id"] == "dec_lead" and r["dst_id"] == "dec_follow" for r in rows)


def test_extract_no_signal_emits_nothing(conn: sqlite3.Connection) -> None:
    """Two series that fire on the same day every time have perfect
    auto-correlation but zero lead-lag advantage → no link emitted."""
    for days in (10, 7, 4, 1):
        _record_usage(
            conn,
            src_type="decision",
            src_id="dec_a",
            days_ago=days,
            bucket_id=f"uf_a_{days}_x",
        )
        _record_usage(
            conn,
            src_type="decision",
            src_id="dec_b",
            days_ago=days,
            bucket_id=f"uf_b_{days}_x",
        )
    result = extract_granger_links(conn, workspace_id="ws")
    # Both series may be active, but neither leads the other.
    assert result.links_emitted == 0
    assert _count_granger(conn) == 0


def test_min_activity_gate(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """A series with one spike is below the floor and skipped before
    the correlation step — keeps noise out."""
    monkeypatch.setenv("MEMORY_CAUSAL_GRANGER_MIN_ACTIVITY", "3")
    _record_usage(conn, src_type="decision", src_id="dec_solo", days_ago=2)
    for days in (5, 3, 1):
        _record_usage(conn, src_type="decision", src_id="dec_busy", days_ago=days)
    result = extract_granger_links(conn, workspace_id="ws")
    assert result.pairs_scanned == 0  # single-active-series can't form a pair


def test_disabled_returns_zero(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_CAUSAL_GRANGER_ENABLED", "false")
    for days in (8, 5, 2):
        _record_usage(conn, src_type="decision", src_id="dec_lead", days_ago=days)
    for days in (7, 4, 1):
        _record_usage(conn, src_type="decision", src_id="dec_follow", days_ago=days)
    result = extract_granger_links(conn, workspace_id="ws")
    assert result == GrangerReport(pairs_scanned=0, links_emitted=0)


def test_workspace_isolation(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Activity in workspace A must not produce links in B."""
    monkeypatch.setenv("MEMORY_CAUSAL_GRANGER_THRESHOLD", "0.2")
    for days in (8, 5, 2):
        _record_usage(
            conn,
            src_type="decision",
            src_id="dec_lead",
            days_ago=days,
            workspace_id="ws_a",
        )
    for days in (7, 4, 1):
        _record_usage(
            conn,
            src_type="decision",
            src_id="dec_follow",
            days_ago=days,
            workspace_id="ws_a",
        )
    extract_granger_links(conn, workspace_id="ws_b")
    assert _count_granger(conn, "ws_b") == 0
    extract_granger_links(conn, workspace_id="ws_a")
    assert _count_granger(conn, "ws_a") >= 1


def test_idempotent_rerun(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running on the same data emits zero new links (UNIQUE on
    causal_links keeps it idempotent)."""
    monkeypatch.setenv("MEMORY_CAUSAL_GRANGER_THRESHOLD", "0.2")
    for days in (8, 5, 2):
        _record_usage(conn, src_type="decision", src_id="dec_lead", days_ago=days)
    for days in (7, 4, 1):
        _record_usage(conn, src_type="decision", src_id="dec_follow", days_ago=days)
    first = extract_granger_links(conn, workspace_id="ws")
    second = extract_granger_links(conn, workspace_id="ws")
    assert second.links_emitted == 0
    assert _count_granger(conn) == first.links_emitted


def test_failure_soft_when_table_missing() -> None:
    """Pre-migration DB without memory_usage_feedback returns an empty
    report instead of raising."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        result = extract_granger_links(c, workspace_id="ws")
        assert result == GrangerReport(pairs_scanned=0, links_emitted=0)
    finally:
        c.close()
