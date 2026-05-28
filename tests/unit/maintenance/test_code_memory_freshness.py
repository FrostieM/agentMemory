from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition.codebase_scan import source_file_sha1
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.maintenance.code_memory_freshness import code_memory_freshness_check
from agent_memory_lite.maintenance.integrity import run_integrity_audit


@pytest.fixture
def project_db(tmp_path: Path) -> Iterator[tuple[Path, Path, sqlite3.Connection]]:
    project = tmp_path / "repo"
    db_dir = project / ".agent_memory"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "memory.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    try:
        yield project, db_path, conn
    finally:
        conn.close()


def _insert_digest(
    conn: sqlite3.Connection,
    *,
    path: str,
    file_sha1: str,
    workspace_id: str = "ws",
) -> None:
    conn.execute(
        """INSERT INTO code_digests (id, workspace_id, file_path, file_sha1,
                                     language, chunk_count, symbol_count,
                                     inbound_edge_count, outbound_edge_count,
                                     purpose_short, top_symbols_json,
                                     last_indexed_at, updated_at)
           VALUES (?, ?, ?, ?, 'python', 1, 1, 0, 0, 'source file',
                   '[]', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""",
        (f"dig_{path.replace('/', '_').replace('.', '_')}", workspace_id, path, file_sha1),
    )


def test_code_memory_freshness_ok_when_digest_hashes_match(
    project_db: tuple[Path, Path, sqlite3.Connection],
) -> None:
    project, _db_path, conn = project_db
    file_path = project / "src" / "foo.py"
    file_path.parent.mkdir()
    file_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    _insert_digest(conn, path="src/foo.py", file_sha1=source_file_sha1(file_path))

    check = code_memory_freshness_check(conn, "ws", project_root=project)

    assert check.status == "ok"
    assert check.details["missing_digests"] == 0
    assert check.details["stale_digests"] == 0


def test_code_memory_freshness_degrades_for_missing_and_stale_digests(
    project_db: tuple[Path, Path, sqlite3.Connection],
) -> None:
    project, _db_path, conn = project_db
    src = project / "src"
    src.mkdir()
    changed = src / "changed.py"
    missing = src / "missing.py"
    changed.write_text("def changed():\n    return 2\n", encoding="utf-8")
    missing.write_text("def missing():\n    return 3\n", encoding="utf-8")
    _insert_digest(conn, path="src/changed.py", file_sha1="0" * 40)

    check = code_memory_freshness_check(conn, "ws", project_root=project)

    assert check.status == "degraded"
    assert check.details["missing_digests"] == 1
    assert check.details["stale_digests"] == 1
    assert check.details["missing_digests_sample"] == ["src/missing.py"]
    assert check.details["stale_digests_sample"][0]["file_path"] == "src/changed.py"


def test_code_memory_freshness_warns_for_orphaned_digest(
    project_db: tuple[Path, Path, sqlite3.Connection],
) -> None:
    project, _db_path, conn = project_db
    _insert_digest(conn, path="src/deleted.py", file_sha1="0" * 40)

    check = code_memory_freshness_check(conn, "ws", project_root=project)

    assert check.status == "warning"
    assert check.details["orphaned_digests"] == 1
    assert check.details["orphaned_digests_sample"] == ["src/deleted.py"]


def test_integrity_audit_includes_code_memory_freshness(
    project_db: tuple[Path, Path, sqlite3.Connection],
) -> None:
    project, db_path, conn = project_db
    file_path = project / "src" / "foo.py"
    file_path.parent.mkdir()
    file_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    _insert_digest(conn, path="src/foo.py", file_sha1=source_file_sha1(file_path))

    report = run_integrity_audit(conn, workspace_id="ws", db_path=db_path)

    assert report.checks["code_memory_freshness"].status == "ok"
    assert report.checks["code_memory_freshness"].details["checked"] is True
