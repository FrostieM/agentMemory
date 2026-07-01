"""Durable code-digest refresh worker (release plan step 10.5).

``refresh_stale_digests`` re-verifies a bounded, rotating batch of existing
code digests and recomputes the stale ones, so a digest that drifted from its
file (queue drop / out-of-hook edit / 120s pre-commit cutoff) self-heals
instead of returning a stale ``impact_check`` verdict.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_memory_lite.config.workspace_registry as wr
import agent_memory_lite.maintenance.digest_refresh as dr
from agent_memory_lite.cognition.digest_worker import compute_digest, upsert_digest
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.maintenance.brain_pass import BrainPassReport, _step_refresh_digests
from agent_memory_lite.maintenance.digest_refresh import (
    DigestRefreshStats,
    refresh_stale_digests,
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "m.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    apply_migrations(c, MIGRATION_DIR)
    yield c
    c.close()


def _seed(
    conn: sqlite3.Connection,
    root: Path,
    rel: str,
    content: str,
    *,
    last_indexed: str = "2020-01-01T00:00:00Z",
) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    upsert_digest(conn, workspace_id="w", result=compute_digest(rel, content))
    conn.execute(
        "UPDATE code_digests SET last_indexed_at = ? WHERE workspace_id = 'w' AND file_path = ?",
        (last_indexed, rel),
    )
    conn.commit()


def _digest(conn: sqlite3.Connection, rel: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT file_sha1, top_symbols_json, last_indexed_at FROM code_digests "
        "WHERE workspace_id = 'w' AND file_path = ?",
        (rel,),
    ).fetchone()
    assert row is not None
    return row


def test_stale_digest_is_recomputed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _seed(conn, root, "a.py", "def old_func():\n    pass\n")
    (root / "a.py").write_text("def brand_new_func():\n    pass\n", encoding="utf-8")  # drift

    stats = refresh_stale_digests(conn, workspace_id="w", project_root=root, limit=10)

    assert stats.refreshed == 1
    assert stats.checked == 1
    row = _digest(conn, "a.py")
    expected = compute_digest("a.py", "def brand_new_func():\n    pass\n")
    assert row["file_sha1"] == expected.file_sha1  # now matches the new content
    assert "brand_new_func" in row["top_symbols_json"]


def test_fresh_digest_is_verified_not_rewritten(conn: sqlite3.Connection, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _seed(conn, root, "a.py", "def f():\n    pass\n")  # file unchanged on disk

    stats = refresh_stale_digests(conn, workspace_id="w", project_root=root, limit=10)

    assert stats.verified == 1
    assert stats.refreshed == 0
    # last_indexed_at was bumped (rotation) even though content was unchanged.
    assert _digest(conn, "a.py")["last_indexed_at"] != "2020-01-01T00:00:00Z"


def test_missing_file_is_unreadable_and_rotated(conn: sqlite3.Connection, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _seed(conn, root, "gone.py", "x = 1\n")
    (root / "gone.py").unlink()  # file deleted but digest row remains

    stats = refresh_stale_digests(conn, workspace_id="w", project_root=root, limit=10)

    assert stats.unreadable == 1
    assert stats.refreshed == 0
    # Touched so it rotates out of the front and never blocks the queue.
    assert _digest(conn, "gone.py")["last_indexed_at"] != "2020-01-01T00:00:00Z"


def test_bounded_by_limit(conn: sqlite3.Connection, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    for i in range(5):
        _seed(conn, root, f"f{i}.py", f"x = {i}\n", last_indexed=f"2020-01-0{i + 1}T00:00:00Z")

    stats = refresh_stale_digests(conn, workspace_id="w", project_root=root, limit=2)

    assert stats.checked == 2  # only the batch limit, despite 5 digests


def test_rotation_checks_oldest_first(conn: sqlite3.Connection, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _seed(conn, root, "old.py", "x = 1\n", last_indexed="2020-01-01T00:00:00Z")
    _seed(conn, root, "new.py", "y = 1\n", last_indexed="2025-01-01T00:00:00Z")

    stats = refresh_stale_digests(conn, workspace_id="w", project_root=root, limit=1)

    assert stats.checked == 1
    assert _digest(conn, "old.py")["last_indexed_at"] != "2020-01-01T00:00:00Z"  # checked + touched
    assert (
        _digest(conn, "new.py")["last_indexed_at"] == "2025-01-01T00:00:00Z"
    )  # untouched this pass


def test_absolute_stored_path_is_resolved(conn: sqlite3.Connection, tmp_path: Path) -> None:
    # A digest stored under an absolute path is read directly, not joined to root.
    root = tmp_path / "proj"
    root.mkdir()
    abs_file = tmp_path / "elsewhere.py"
    abs_file.write_text("def g():\n    pass\n", encoding="utf-8")
    upsert_digest(
        conn, workspace_id="w", result=compute_digest(str(abs_file), "def OLD():\n    pass\n")
    )
    conn.commit()

    stats = refresh_stale_digests(conn, workspace_id="w", project_root=root, limit=10)
    assert stats.refreshed == 1


def test_no_project_root_is_noop(conn: sqlite3.Connection) -> None:
    _seed_root = None
    stats = refresh_stale_digests(conn, workspace_id="w", project_root=_seed_root, limit=10)
    assert stats == DigestRefreshStats()


def test_missing_table_is_noop(tmp_path: Path) -> None:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        stats = refresh_stale_digests(c, workspace_id="w", project_root=tmp_path, limit=10)
        assert stats == DigestRefreshStats()
    finally:
        c.close()


def test_value_error_on_read_is_unreadable_not_raised(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pathological stored path (e.g. embedded NUL) makes read_text raise
    # ValueError, not OSError. The probe must treat it as unreadable and never
    # let it escape -- the worker's "never raises" contract.
    root = tmp_path / "proj"
    root.mkdir()
    _seed(conn, root, "a.py", "x = 1\n")  # seed BEFORE patching read_text

    def _boom(*_a: object, **_k: object) -> str:
        raise ValueError("embedded null character")

    monkeypatch.setattr(Path, "read_text", _boom)
    stats = refresh_stale_digests(conn, workspace_id="w", project_root=root, limit=10)
    assert stats.unreadable == 1
    assert stats.refreshed == 0


def test_closed_connection_does_not_raise(tmp_path: Path) -> None:
    # table_exists on a closed conn raises ProgrammingError (an sqlite3.Error);
    # the worker must swallow it (the "never raises" contract), not propagate.
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.close()
    stats = refresh_stale_digests(c, workspace_id="w", project_root=tmp_path, limit=10)
    assert stats == DigestRefreshStats()


# ---- brain_pass wiring (_step_refresh_digests) ---------------------------


def test_brain_step_disabled_is_noop(
    conn: sqlite3.Connection, settings_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Disabled gate short-circuits BEFORE any registry access.
    def _no_registry(*_a: object, **_k: object) -> object:
        raise AssertionError("registry must not be touched when disabled")

    monkeypatch.setattr(wr, "WorkspaceRegistry", _no_registry)
    settings = settings_factory(MEMORY_DIGEST_REFRESH_ENABLED="false")
    report = BrainPassReport(workspace_id="w", started_at="t", finished_at="t")
    _step_refresh_digests(conn, "w", settings, report)
    assert report.digests_checked == 0
    assert report.digests_refreshed == 0
    assert not report.errors


def test_brain_step_wires_registry_root_and_report(
    conn: sqlite3.Connection, settings_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def _fake_registry(*_a: object, **_k: object) -> object:
        return SimpleNamespace(get=lambda _wid: SimpleNamespace(project_root="/some/root"))

    def _spy(
        _conn: object, *, workspace_id: str, project_root: object, limit: int, commit: bool = True
    ) -> DigestRefreshStats:
        captured["project_root"] = project_root
        captured["limit"] = limit
        captured["commit"] = commit
        return DigestRefreshStats(checked=3, refreshed=2)

    monkeypatch.setattr(wr, "WorkspaceRegistry", _fake_registry)
    monkeypatch.setattr(dr, "refresh_stale_digests", _spy)
    settings = settings_factory(
        MEMORY_DIGEST_REFRESH_ENABLED="true", MEMORY_DIGEST_REFRESH_MAX_PER_PASS="7"
    )
    report = BrainPassReport(workspace_id="w", started_at="t", finished_at="t")
    _step_refresh_digests(conn, "w", settings, report)
    assert str(captured["project_root"]) == str(Path("/some/root"))
    assert captured["limit"] == 7
    assert report.digests_checked == 3
    assert report.digests_refreshed == 2
