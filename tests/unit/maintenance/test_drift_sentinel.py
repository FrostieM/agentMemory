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


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_DRIFT_SENTINEL_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_DRIFT_COVERAGE_MIN", raising=False)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Production-like canonical v3 schema.

    FK enforcement is disabled in this fixture so tests can seed
    orphan chunks directly (same state production lands in when a
    file is deleted but its chunks linger)."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    c = open_connection(tmp_path / "drift.db")
    apply_migrations(c)
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


def _seed_capability_link(
    conn: sqlite3.Connection,
    *,
    link_id: str,
    capability_type: str,
    capability_id: str,
    capability_name: str = "missing",
    target_type: str = "decision",
    target_id: str = "dec_x",
    ws: str = "ws",
) -> None:
    """Insert a capability_link row directly. Useful for orphan-seeding:
    the test can point ``capability_id`` at a row that was never inserted
    into skills rows, mimicking the production state
    where a capability got deleted but its links survived."""
    conn.execute(
        """INSERT INTO capability_links
           (id, workspace_id, target_type, target_id,
            capability_type, capability_id, capability_name,
            relation, strength, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'method', 0.7,
                   '2026-05-20T00:00:00+00:00',
                   '2026-05-20T00:00:00+00:00')""",
        (
            link_id,
            ws,
            target_type,
            target_id,
            capability_type,
            capability_id,
            capability_name,
        ),
    )
    conn.commit()


def _seed_real_skill(conn: sqlite3.Connection, *, skill_id: str, ws: str = "ws") -> None:
    """Insert a skills(subtype='skill') row so a capability_link pointing at it is
    NOT dangling. Mirrors the minimal columns the test corpus needs."""
    conn.execute(
        """INSERT INTO skills (id, workspace_id, name, subtype, summary,
                               active, created_at, updated_at)
           VALUES (?, ?, 'fixture-skill', 'skill', 'fixture', 1,
                   '2026-05-20T00:00:00+00:00',
                   '2026-05-20T00:00:00+00:00')""",
        (skill_id, ws),
    )
    conn.commit()


def test_capability_links_drift_detected_and_resolved(conn: sqlite3.Connection) -> None:
    """Dangling capability_link (capability_id pointing at missing skill)
    → finding. Once the skill row appears, next pass resolves the event.
    This is the v3.4 follow-up to today's audit-cleanup (theory
    th_6bbb2cf024961a0f predicted exactly this check)."""
    _seed_capability_link(
        conn,
        link_id="caplink_orphan_1",
        capability_type="skill",
        capability_id="skill_missing",
    )
    r1 = detect_drift(conn, workspace_id="ws")
    assert "capability_links:1" in r1.findings
    events = _open_events(conn)
    assert any(kind == "memory_drift_capability_links" for kind, _ in events)
    # Materialize the missing skill → next pass resolves the event.
    _seed_real_skill(conn, skill_id="skill_missing")
    r2 = detect_drift(conn, workspace_id="ws")
    assert "capability_links" in r2.resolved
    assert all(kind != "memory_drift_capability_links" for kind, _ in _open_events(conn))


def test_capability_links_drift_recurrence(conn: sqlite3.Connection) -> None:
    """Repeated runs over the same dangling rows bump recurrence_count,
    not duplicate rows. Same idempotency contract as the fk check."""
    _seed_capability_link(
        conn,
        link_id="caplink_orphan_2",
        capability_type="playbook",
        capability_id="play_missing",
    )
    detect_drift(conn, workspace_id="ws")
    detect_drift(conn, workspace_id="ws")
    rows = conn.execute(
        "SELECT recurrence_count FROM maintenance_events "
        "WHERE workspace_id = 'ws' AND kind = 'memory_drift_capability_links'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 2  # 1 insert + 1 bump


def test_capability_links_check_is_workspace_scoped(conn: sqlite3.Connection) -> None:
    """A dangling link in workspace A must not flag workspace B."""
    _seed_capability_link(
        conn,
        link_id="caplink_orphan_a",
        capability_type="role",
        capability_id="role_missing",
        ws="ws_a",
    )
    detect_drift(conn, workspace_id="ws_b")
    assert all(kind != "memory_drift_capability_links" for kind, _ in _open_events(conn, ws="ws_b"))
    detect_drift(conn, workspace_id="ws_a")
    assert any(kind == "memory_drift_capability_links" for kind, _ in _open_events(conn, ws="ws_a"))


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
