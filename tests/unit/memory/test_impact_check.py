"""Unit tests for v3 impact_check — the discipline primitive.

Covers:

* ``not_indexed`` verdict when file has no code_digests row
* ``low`` verdict for indexed files with zero callers
* ``medium`` verdict for 1-5 callers
* ``high`` verdict for 6+ callers OR any hot symbol
* Staleness flag prepends a warning to the advisory
* Schema-mismatch / SQL errors return graceful not_indexed
* Caller list capped to ``callers_limit``
* Hot symbols ranked by callers_count, capped at 10
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition.codebase_scan import source_file_sha1
from agent_memory_lite.cognition.impact_check import (
    ImpactReport,
    _compute_verdict,
    _is_stale,
    impact_check,
)


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


def _now_iso(offset_seconds: int = 0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_seconds))


def _seed_file(
    conn: sqlite3.Connection, *, file_id: str, path: str, workspace_id: str = "ws"
) -> None:
    conn.execute(
        """INSERT INTO files (id, workspace_id, path, language, content_hash,
                              size_bytes, last_indexed_at, is_archived)
           VALUES (?, ?, ?, 'python', ?, 100, ?, 0)""",
        (file_id, workspace_id, path, "h_" + file_id, _now_iso()),
    )


def _seed_chunk(
    conn: sqlite3.Connection,
    *,
    chunk_id: str,
    file_id: str,
    qualified_name: str,
    workspace_id: str = "ws",
) -> None:
    conn.execute(
        """INSERT INTO chunks (id, workspace_id, file_id, kind, text, gist,
                               line_start, line_end, qualified_name,
                               symbol_kind, importance, confidence,
                               is_archived, created_at)
           VALUES (?, ?, ?, 'symbol', 'body', 'gist', 1, 5, ?, 'function',
                   0.5, 0.5, 0, ?)""",
        (chunk_id, workspace_id, file_id, qualified_name, _now_iso()),
    )


def _seed_edge(
    conn: sqlite3.Connection,
    *,
    edge_id: str,
    src_chunk_id: str,
    src_qualified_name: str,
    dst_chunk_id: str,
    dst_qualified_name: str,
    edge_type: str = "calls",
    workspace_id: str = "ws",
) -> None:
    conn.execute(
        """INSERT INTO symbol_edges (id, workspace_id, src_chunk_id,
                                     src_qualified_name, dst_qualified_name,
                                     dst_chunk_id, edge_type, src_language,
                                     created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'python', ?)""",
        (
            edge_id,
            workspace_id,
            src_chunk_id,
            src_qualified_name,
            dst_qualified_name,
            dst_chunk_id,
            edge_type,
            _now_iso(),
        ),
    )


def _seed_digest(
    conn: sqlite3.Connection,
    *,
    digest_id: str,
    file_path: str,
    inbound_edge_count: int = 0,
    indexed_minutes_ago: int = 0,
    file_sha1: str | None = None,
    workspace_id: str = "ws",
) -> None:
    ts = _now_iso(-indexed_minutes_ago * 60)
    conn.execute(
        """INSERT INTO code_digests (id, workspace_id, file_path, file_sha1,
                                     language, chunk_count, symbol_count,
                                     inbound_edge_count, outbound_edge_count,
                                     purpose_short, top_symbols_json,
                                     last_indexed_at, updated_at)
           VALUES (?, ?, ?, ?, 'python', 3, 2, ?, 0, 'compute fees',
                   '[]', ?, ?)""",
        (
            digest_id,
            workspace_id,
            file_path,
            file_sha1 if file_sha1 is not None else ("abc" * 13 + "a"),
            inbound_edge_count,
            ts,
            ts,
        ),
    )


# ============================================================
# _compute_verdict
# ============================================================


def test_compute_verdict_not_indexed() -> None:
    assert _compute_verdict(indexed=False, callers_count=0, hot_count=0) == "not_indexed"


def test_compute_verdict_low() -> None:
    assert _compute_verdict(indexed=True, callers_count=0, hot_count=0) == "low"


def test_compute_verdict_medium() -> None:
    assert _compute_verdict(indexed=True, callers_count=3, hot_count=0) == "medium"


def test_compute_verdict_high_by_count() -> None:
    assert _compute_verdict(indexed=True, callers_count=6, hot_count=0) == "high"


def test_compute_verdict_high_by_hot_symbol() -> None:
    # Even 1 caller can be high if there's a hot symbol.
    assert _compute_verdict(indexed=True, callers_count=1, hot_count=1) == "high"


# ============================================================
# _is_stale
# ============================================================


def test_is_stale_empty_timestamp() -> None:
    assert _is_stale("") is True
    assert _is_stale(None) is True


def test_is_stale_fresh_timestamp() -> None:
    fresh = _now_iso(-60)  # 60s ago
    assert _is_stale(fresh) is False


def test_is_stale_old_timestamp() -> None:
    old = _now_iso(-180 * 60)  # 3h ago
    assert _is_stale(old) is True


def test_is_stale_malformed_timestamp() -> None:
    assert _is_stale("not-a-timestamp") is True


# ============================================================
# impact_check — verdict scenarios
# ============================================================


def test_not_indexed_when_no_digest(conn: sqlite3.Connection) -> None:
    report = impact_check(conn, workspace_id="ws", file_path="src/missing.py")
    assert report.verdict == "not_indexed"
    assert "not in code_digests" in report.advisory


def test_low_verdict_when_indexed_zero_callers(conn: sqlite3.Connection) -> None:
    _seed_digest(conn, digest_id="d_low", file_path="src/low.py", inbound_edge_count=0)
    report = impact_check(conn, workspace_id="ws", file_path="src/low.py")
    assert report.verdict == "low"
    assert report.callers == []
    assert "Safe to edit" in report.advisory


def test_medium_verdict_for_three_callers(conn: sqlite3.Connection) -> None:
    """3 callers spread across 3 different target symbols → medium verdict.

    Each target symbol has only 1 caller, so no symbol crosses the
    ``hot_threshold=3`` bar — verdict stays medium, not promoted to high.
    """
    _seed_file(conn, file_id="f_tgt", path="src/tgt.py")
    # 3 separate symbols in the target file so callers spread out.
    for j in range(3):
        _seed_chunk(
            conn,
            chunk_id=f"c_tgt_{j}",
            file_id="f_tgt",
            qualified_name=f"tgt.sym_{j}",
        )
    for i in range(3):
        _seed_file(conn, file_id=f"f_caller_{i}", path=f"src/caller_{i}.py")
        _seed_chunk(
            conn,
            chunk_id=f"c_caller_{i}",
            file_id=f"f_caller_{i}",
            qualified_name=f"caller_{i}.use",
        )
        _seed_edge(
            conn,
            edge_id=f"e_{i}",
            src_chunk_id=f"c_caller_{i}",
            src_qualified_name=f"caller_{i}.use",
            dst_chunk_id=f"c_tgt_{i}",
            dst_qualified_name=f"tgt.sym_{i}",
        )
    _seed_digest(conn, digest_id="d_tgt", file_path="src/tgt.py", inbound_edge_count=3)
    report = impact_check(conn, workspace_id="ws", file_path="src/tgt.py")
    assert report.verdict == "medium"
    assert len(report.callers) == 3
    assert report.hot_symbols == []  # spread, not concentrated
    assert "1-5 callers" in report.advisory


def test_high_verdict_for_six_callers(conn: sqlite3.Connection) -> None:
    _seed_file(conn, file_id="f_tgt", path="src/hub.py")
    _seed_chunk(conn, chunk_id="c_tgt", file_id="f_tgt", qualified_name="hub.foo")
    for i in range(6):
        _seed_file(conn, file_id=f"f_c_{i}", path=f"src/c_{i}.py")
        _seed_chunk(
            conn,
            chunk_id=f"c_c_{i}",
            file_id=f"f_c_{i}",
            qualified_name=f"c_{i}.use",
        )
        _seed_edge(
            conn,
            edge_id=f"e_{i}",
            src_chunk_id=f"c_c_{i}",
            src_qualified_name=f"c_{i}.use",
            dst_chunk_id="c_tgt",
            dst_qualified_name="hub.foo",
        )
    _seed_digest(conn, digest_id="d_hub", file_path="src/hub.py", inbound_edge_count=6)
    report = impact_check(conn, workspace_id="ws", file_path="src/hub.py")
    assert report.verdict == "high"
    assert "HIGH impact" in report.advisory


def test_hot_symbols_promoted_to_high(conn: sqlite3.Connection) -> None:
    """3 callers but all hitting one symbol → hot symbol → high verdict."""
    _seed_file(conn, file_id="f_tgt", path="src/hot.py")
    _seed_chunk(conn, chunk_id="c_tgt", file_id="f_tgt", qualified_name="hot.crit")
    for i in range(3):
        _seed_file(conn, file_id=f"f_c_{i}", path=f"src/c_{i}.py")
        _seed_chunk(
            conn,
            chunk_id=f"c_c_{i}",
            file_id=f"f_c_{i}",
            qualified_name=f"c_{i}.use",
        )
        _seed_edge(
            conn,
            edge_id=f"e_{i}",
            src_chunk_id=f"c_c_{i}",
            src_qualified_name=f"c_{i}.use",
            dst_chunk_id="c_tgt",
            dst_qualified_name="hot.crit",  # all 3 callers hit the same dst
        )
    _seed_digest(conn, digest_id="d_hot", file_path="src/hot.py", inbound_edge_count=3)
    report = impact_check(conn, workspace_id="ws", file_path="src/hot.py")
    # 3 callers normally medium, but the single hot symbol promotes to high.
    assert report.verdict == "high"
    assert len(report.hot_symbols) == 1
    assert report.hot_symbols[0].qualified_name == "hot.crit"
    assert report.hot_symbols[0].callers_count == 3


# ============================================================
# Staleness + edge cases
# ============================================================


def test_stale_digest_prepends_warning(conn: sqlite3.Connection) -> None:
    _seed_digest(
        conn,
        digest_id="d_stale",
        file_path="src/stale.py",
        inbound_edge_count=0,
        indexed_minutes_ago=180,
    )
    report = impact_check(conn, workspace_id="ws", file_path="src/stale.py")
    assert report.verdict == "low"
    assert report.advisory.startswith("[stale digest")
    assert report.digest["is_stale"] is True


def test_old_digest_is_not_stale_when_file_sha_matches(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    project_root = tmp_path / "repo"
    file_path = project_root / "src" / "fresh.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("def fresh():\n    return 1\n", encoding="utf-8")
    _seed_digest(
        conn,
        digest_id="d_fresh",
        file_path="src/fresh.py",
        indexed_minutes_ago=180,
        file_sha1=source_file_sha1(file_path),
    )

    report = impact_check(
        conn,
        workspace_id="ws",
        file_path="src/fresh.py",
        project_root=project_root,
    )

    assert report.digest["is_stale"] is False
    assert not report.advisory.startswith("[stale digest")


def test_digest_is_stale_when_file_sha_differs(conn: sqlite3.Connection, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    file_path = project_root / "src" / "changed.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("def changed():\n    return 2\n", encoding="utf-8")
    _seed_digest(
        conn,
        digest_id="d_changed",
        file_path="src/changed.py",
        file_sha1="0" * 40,
    )

    report = impact_check(
        conn,
        workspace_id="ws",
        file_path="src/changed.py",
        project_root=project_root,
    )

    assert report.digest["is_stale"] is True
    assert report.digest["stale_reason"] == "sha_mismatch"
    assert "file_sha1 differs" in report.advisory


def test_digest_is_stale_when_file_sha_missing(conn: sqlite3.Connection, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    file_path = project_root / "src" / "missing_hash.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("def missing_hash():\n    return 3\n", encoding="utf-8")
    _seed_digest(
        conn,
        digest_id="d_missing_hash",
        file_path="src/missing_hash.py",
        file_sha1="",
    )

    report = impact_check(
        conn,
        workspace_id="ws",
        file_path="src/missing_hash.py",
        project_root=project_root,
    )

    assert report.digest["is_stale"] is True
    assert report.digest["stale_reason"] == "missing_file_sha1"
    assert "file_sha1 missing" in report.advisory


def test_callers_capped_at_limit(conn: sqlite3.Connection) -> None:
    _seed_file(conn, file_id="f_tgt", path="src/many.py")
    _seed_chunk(conn, chunk_id="c_tgt", file_id="f_tgt", qualified_name="many.foo")
    for i in range(10):
        _seed_file(conn, file_id=f"f_c_{i}", path=f"src/c_{i}.py")
        _seed_chunk(
            conn,
            chunk_id=f"c_c_{i}",
            file_id=f"f_c_{i}",
            qualified_name=f"c_{i}.use",
        )
        _seed_edge(
            conn,
            edge_id=f"e_{i}",
            src_chunk_id=f"c_c_{i}",
            src_qualified_name=f"c_{i}.use",
            dst_chunk_id="c_tgt",
            dst_qualified_name="many.foo",
        )
    _seed_digest(conn, digest_id="d_many", file_path="src/many.py", inbound_edge_count=10)
    report = impact_check(conn, workspace_id="ws", file_path="src/many.py", callers_limit=4)
    assert len(report.callers) == 4


def test_report_to_dict_shape(conn: sqlite3.Connection) -> None:
    _seed_digest(conn, digest_id="d_x", file_path="src/x.py", inbound_edge_count=0)
    report = impact_check(conn, workspace_id="ws", file_path="src/x.py")
    out = report.to_dict()
    assert set(out.keys()) == {
        "file_path",
        "digest",
        "callers",
        "hot_symbols",
        "verdict",
        "advisory",
    }


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    """Digest in workspace A must not bleed into workspace B's report."""
    _seed_digest(
        conn,
        digest_id="d_a",
        file_path="src/iso.py",
        inbound_edge_count=0,
        workspace_id="ws_a",
    )
    # ws_b never seeded this file.
    report = impact_check(conn, workspace_id="ws_b", file_path="src/iso.py")
    assert report.verdict == "not_indexed"


def test_absolute_path_resolves_to_canonical_relative_digest(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    project_root = tmp_path / "repo"
    file_path = project_root / "src" / "foo.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    _seed_file(conn, file_id="f_abs", path="src/foo.py")
    _seed_chunk(conn, chunk_id="c_abs", file_id="f_abs", qualified_name="foo")
    _seed_digest(conn, digest_id="d_abs", file_path="src/foo.py")

    report = impact_check(
        conn,
        workspace_id="ws",
        file_path=str(file_path),
        project_root=project_root,
    )

    assert report.verdict == "low"
    assert report.file_path == "src/foo.py"
    assert report.digest["file_path"] == "src/foo.py"


def test_deleted_digest_is_not_trusted(conn: sqlite3.Connection, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    _seed_digest(conn, digest_id="d_deleted", file_path="src/deleted.py")

    report = impact_check(
        conn,
        workspace_id="ws",
        file_path="src/deleted.py",
        project_root=project_root,
    )

    assert report.verdict == "not_indexed"


def test_empty_impact_report_dataclass() -> None:
    """Default-constructed ImpactReport is internally consistent."""
    r = ImpactReport(file_path="x.py")
    out = r.to_dict()
    assert out["verdict"] == "not_indexed"
    assert out["digest"] == {}
    assert out["callers"] == []
    assert out["hot_symbols"] == []
