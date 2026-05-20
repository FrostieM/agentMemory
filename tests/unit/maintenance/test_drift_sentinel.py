"""v3.4 drift sentinel tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.maintenance.drift_sentinel import (
    DriftReport,
    coverage_threshold,
    detect_drift,
    is_enabled,
)

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_DRIFT_SENTINEL_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_DRIFT_COVERAGE_MIN", raising=False)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Production-like hybrid schema: legacy migrations create chunks/
    files/chunks_fts; canonical layers maintenance_events on top.

    FK enforcement is disabled in this fixture so tests can seed
    orphan chunks directly (same state production lands in when a
    file is deleted but its chunks linger)."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    c = open_connection(tmp_path / "drift.db")
    apply_migrations(c)
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.execute("PRAGMA foreign_keys = OFF")
    try:
        yield c
    finally:
        c.close()


def _seed_chunk(
    conn: sqlite3.Connection,
    *,
    chunk_id: str,
    file_id: str | None = None,
    embedding_id: str | None = None,
    ws: str = "ws",
) -> None:
    conn.execute(
        "INSERT INTO chunks (id, workspace_id, file_id, text, embedding_id, kind, created_at) "
        "VALUES (?, ?, ?, 'text', ?, 'code', '2026-05-20T00:00:00+00:00')",
        (chunk_id, ws, file_id, embedding_id),
    )
    conn.commit()


def _seed_file(conn: sqlite3.Connection, *, file_id: str, ws: str = "ws") -> None:
    """Insert just the NOT NULL columns of the legacy ``files`` table."""
    conn.execute(
        "INSERT INTO files (id, workspace_id, path, language, content_hash, "
        "size_bytes, last_indexed_at) "
        "VALUES (?, ?, ?, 'python', 'sha:fixture', 0, '2026-05-20T00:00:00+00:00')",
        (file_id, ws, f"src/{file_id}.py"),
    )
    conn.commit()


def _open_events(conn: sqlite3.Connection, *, ws: str = "ws") -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT kind, summary FROM maintenance_events WHERE workspace_id = ? AND status = 'open'",
        (ws,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def test_defaults() -> None:
    assert is_enabled() is True
    assert coverage_threshold() == pytest.approx(0.90)


def test_no_drift_emits_nothing(conn: sqlite3.Connection) -> None:
    """Empty workspace → nothing to flag."""
    result = detect_drift(conn, workspace_id="ws")
    assert result.findings == []
    assert _open_events(conn) == []


def test_fk_drift_detected_and_resolved(conn: sqlite3.Connection) -> None:
    """Orphan chunks (file_id pointing at missing file) → finding."""
    _seed_chunk(conn, chunk_id="c1", file_id="file_missing")
    r1 = detect_drift(conn, workspace_id="ws")
    assert "fk:1" in r1.findings
    events = _open_events(conn)
    assert any(kind == "memory_drift_fk" for kind, _ in events)
    # Clean up the orphan → next detect_drift resolves the event.
    conn.execute("DELETE FROM chunks WHERE id = 'c1'")
    conn.commit()
    r2 = detect_drift(conn, workspace_id="ws")
    assert "fk" in r2.resolved
    assert all(kind != "memory_drift_fk" for kind, _ in _open_events(conn))


def test_recurrence_count_bumps_not_duplicates(conn: sqlite3.Connection) -> None:
    """Same drift detected on repeated runs → single row, recurrence
    count grows. No duplicates."""
    _seed_chunk(conn, chunk_id="c1", file_id="file_missing")
    detect_drift(conn, workspace_id="ws")
    detect_drift(conn, workspace_id="ws")
    detect_drift(conn, workspace_id="ws")
    rows = conn.execute(
        "SELECT recurrence_count FROM maintenance_events "
        "WHERE workspace_id = 'ws' AND kind = 'memory_drift_fk'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 3  # 1 insert + 2 bumps


def test_fts_coverage_drift_detected(conn: sqlite3.Connection) -> None:
    """Chunks without matching chunks_fts rows below threshold → flag."""
    _seed_file(conn, file_id="f1")
    for i in range(10):
        _seed_chunk(conn, chunk_id=f"c{i}", file_id="f1")
    # Insert only 5 FTS rows (50% coverage, well below 90% threshold).
    for i in range(5):
        conn.execute(
            "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)",
            (i + 1000, f"text {i}"),
        )
    conn.commit()
    result = detect_drift(conn, workspace_id="ws")
    assert any(f.startswith("fts:") for f in result.findings)


def test_vector_coverage_drift_detected(conn: sqlite3.Connection) -> None:
    """Chunks without embedding_id below threshold → flag."""
    _seed_file(conn, file_id="f1")
    for i in range(10):
        _seed_chunk(
            conn,
            chunk_id=f"c{i}",
            file_id="f1",
            embedding_id=f"c{i}" if i < 5 else None,  # 50% coverage
        )
    result = detect_drift(conn, workspace_id="ws")
    assert any(f.startswith("vector:") for f in result.findings)


def test_disabled_short_circuits(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_DRIFT_SENTINEL_ENABLED", "false")
    _seed_chunk(conn, chunk_id="c1", file_id="file_missing")
    result = detect_drift(conn, workspace_id="ws")
    assert result.findings == []


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    """Drift in workspace A must not raise event in workspace B."""
    _seed_chunk(conn, chunk_id="c_a", file_id="missing", ws="ws_a")
    detect_drift(conn, workspace_id="ws_b")
    assert _open_events(conn, ws="ws_b") == []
    detect_drift(conn, workspace_id="ws_a")
    assert any(kind == "memory_drift_fk" for kind, _ in _open_events(conn, ws="ws_a"))


def test_threshold_override(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tighten threshold → previously-OK coverage becomes a finding."""
    _seed_file(conn, file_id="f1")
    for i in range(10):
        _seed_chunk(
            conn,
            chunk_id=f"c{i}",
            file_id="f1",
            embedding_id=f"c{i}" if i < 9 else None,  # 90% coverage
        )
    # At default 0.90 threshold this is exactly at the floor, so no flag.
    monkeypatch.setenv("MEMORY_DRIFT_COVERAGE_MIN", "0.95")
    result = detect_drift(conn, workspace_id="ws")
    assert any(f.startswith("vector:") for f in result.findings)


def test_report_type() -> None:
    """Ensure DriftReport defaults are mutable-safe (separate lists)."""
    r1 = DriftReport()
    r2 = DriftReport()
    r1.findings.append("x")
    assert r2.findings == []
