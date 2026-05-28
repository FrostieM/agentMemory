"""Unit tests for scripts/bulk_index_codebase.py.

Covers:

* ``iter_source_files`` — walks tree, respects skip_dirs + extensions
* ``bulk_index`` — UPSERTs digests for each indexable file
* Idempotency: re-running skips unchanged files (SHA match)
* ``--force`` flag re-indexes even unchanged files
* Skips files whose language detection returns None
* Stores relative_paths by default; ``--absolute-paths`` flips
* Error reporting: missing DB → errors+=1
* End-to-end main() with --json + --human + --force
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from scripts import bulk_index_codebase as bulk


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "canonical.db"
    conn = sqlite3.connect(path)
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(conn)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    (p / "src").mkdir()
    (p / "src" / "foo.py").write_text(
        '"""Compute fees module."""\n\ndef compute(): pass\n', encoding="utf-8"
    )
    (p / "src" / "bar.ts").write_text("export function bar() {}\n", encoding="utf-8")
    (p / "README.md").write_text("# Title\nBody\n", encoding="utf-8")
    # Files in skip dirs — should not be indexed
    (p / ".venv").mkdir()
    (p / ".venv" / "site.py").write_text("# venv internal\n", encoding="utf-8")
    (p / "__pycache__").mkdir()
    (p / "__pycache__" / "x.pyc").write_text("compiled\n", encoding="utf-8")
    # Unsupported extension
    (p / "logo.png").write_text("not really a png", encoding="utf-8")
    return p


# ============================================================
# iter_source_files
# ============================================================


def test_iter_skips_venv_and_pycache(project: Path) -> None:
    files = bulk.iter_source_files(project)
    names = {f.name for f in files}
    assert "foo.py" in names
    assert "bar.ts" in names
    assert "README.md" in names
    assert "site.py" not in names  # under .venv
    assert "x.pyc" not in names  # under __pycache__
    assert "logo.png" not in names  # extension not in DEFAULT_EXTENSIONS


def test_iter_skips_claude_worktrees(project: Path) -> None:
    worktree = project / ".claude" / "worktrees" / "old"
    worktree.mkdir(parents=True)
    (worktree / "ghost.py").write_text("def ghost(): pass\n", encoding="utf-8")

    files = bulk.iter_source_files(project)

    assert worktree / "ghost.py" not in files


def test_iter_only_returns_known_extensions(project: Path) -> None:
    files = bulk.iter_source_files(project, extensions=frozenset({".py"}))
    suffixes = {f.suffix for f in files}
    assert suffixes == {".py"}


# ============================================================
# bulk_index
# ============================================================


def test_bulk_index_indexes_three_files(project: Path, db_path: Path) -> None:
    report = bulk.bulk_index(project, workspace_id="ws", db_path=db_path)
    # foo.py + bar.ts + README.md = 3 indexable files
    assert report.walked >= 3
    assert report.indexed == 3
    assert report.errors == 0
    # Verify rows landed
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT file_path, language FROM code_digests WHERE workspace_id = ?",
        ("ws",),
    ).fetchall()
    conn.close()
    paths = {r[0] for r in rows}
    assert "src/foo.py" in paths  # relative path stored
    assert "src/bar.ts" in paths
    assert "README.md" in paths


def test_bulk_index_is_idempotent_on_second_pass(project: Path, db_path: Path) -> None:
    bulk.bulk_index(project, workspace_id="ws", db_path=db_path)
    second = bulk.bulk_index(project, workspace_id="ws", db_path=db_path)
    # Second pass: all files SHA-match, indexed=0, skipped_unchanged=3
    assert second.indexed == 0
    assert second.skipped_unchanged == 3


def test_bulk_index_force_reindexes_unchanged(project: Path, db_path: Path) -> None:
    bulk.bulk_index(project, workspace_id="ws", db_path=db_path)
    forced = bulk.bulk_index(project, workspace_id="ws", db_path=db_path, force=True)
    assert forced.indexed == 3
    assert forced.skipped_unchanged == 0


def test_bulk_index_detects_modified_files(project: Path, db_path: Path) -> None:
    bulk.bulk_index(project, workspace_id="ws", db_path=db_path)
    # Modify one file → SHA changes → indexed=1
    (project / "src" / "foo.py").write_text('"""Different module body."""\n', encoding="utf-8")
    second = bulk.bulk_index(project, workspace_id="ws", db_path=db_path)
    assert second.indexed == 1
    assert second.skipped_unchanged == 2


def test_bulk_index_absolute_paths(project: Path, db_path: Path) -> None:
    bulk.bulk_index(project, workspace_id="ws", db_path=db_path, relative_paths=False)
    conn = sqlite3.connect(db_path)
    paths = {
        r[0]
        for r in conn.execute(
            "SELECT file_path FROM code_digests WHERE workspace_id = 'ws'"
        ).fetchall()
    }
    conn.close()
    # Absolute paths contain the project directory.
    assert any(str(project) in p for p in paths)


def test_bulk_index_language_breakdown(project: Path, db_path: Path) -> None:
    report = bulk.bulk_index(project, workspace_id="ws", db_path=db_path)
    assert report.languages.get("python") == 1
    assert report.languages.get("typescript") == 1
    assert report.languages.get("markdown") == 1


def test_bulk_index_missing_db_returns_error(project: Path, tmp_path: Path) -> None:
    bogus = tmp_path / "nope.db"
    report = bulk.bulk_index(project, workspace_id="ws", db_path=bogus)
    assert report.errors == 1
    assert "db not found" in report.error_details[0]


def test_bulk_index_workspace_isolation(project: Path, db_path: Path) -> None:
    bulk.bulk_index(project, workspace_id="ws_a", db_path=db_path)
    bulk.bulk_index(project, workspace_id="ws_b", db_path=db_path)
    conn = sqlite3.connect(db_path)
    n_a = conn.execute("SELECT COUNT(*) FROM code_digests WHERE workspace_id='ws_a'").fetchone()[0]
    n_b = conn.execute("SELECT COUNT(*) FROM code_digests WHERE workspace_id='ws_b'").fetchone()[0]
    conn.close()
    assert n_a == 3
    assert n_b == 3


def test_bulk_index_prunes_stale_digest_file_chunks_and_edges(project: Path, db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO files (id, workspace_id, path, language, content_hash,
                              size_bytes, last_indexed_at, is_archived)
           VALUES ('file_stale', 'ws', 'src/deleted.py', 'python', 'old', 10,
                   '2026-01-01T00:00:00Z', 0)"""
    )
    conn.execute(
        """INSERT INTO chunks (id, workspace_id, file_id, kind, text, gist,
                               line_start, line_end, symbols_json, importance,
                               confidence, is_archived, created_at)
           VALUES ('chk_stale', 'ws', 'file_stale', 'symbol', 'old', 'old',
                   1, 1, '[]', 0.5, 0.5, 0, '2026-01-01T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO chunks_fts (chunk_id, workspace_id, path, symbols, text, summary)
           VALUES ('chk_stale', 'ws', 'src/deleted.py', '', 'old', '')"""
    )
    conn.execute(
        """INSERT INTO code_digests (id, workspace_id, file_path, file_sha1,
                                     language, chunk_count, symbol_count,
                                     inbound_edge_count, outbound_edge_count,
                                     purpose_short, top_symbols_json,
                                     last_indexed_at, updated_at)
           VALUES ('dig_stale', 'ws', 'src/deleted.py', 'old', 'python',
                   1, 1, 0, 0, 'old', '[]',
                   '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO symbol_edges (id, workspace_id, src_chunk_id,
                                     src_qualified_name, dst_qualified_name,
                                     dst_chunk_id, edge_type, src_language,
                                     created_at)
           VALUES ('edge_stale', 'ws', 'chk_stale', 'old.use', 'old.target',
                   'chk_stale', 'calls', 'python', '2026-01-01T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO files (id, workspace_id, path, language, content_hash,
                              size_bytes, last_indexed_at, metadata_json, is_archived)
           VALUES ('file_ingested_doc', 'ws', 'docs/uploaded.md', 'markdown', 'doc-hash', 10,
                   '2026-01-01T00:00:00Z', ?, 0)""",
        (json.dumps({"trust_level": "untrusted_doc"}),),
    )
    conn.execute(
        """INSERT INTO chunks (id, workspace_id, file_id, kind, text, gist,
                               line_start, line_end, symbols_json, importance,
                               confidence, is_archived, created_at)
           VALUES ('chk_ingested_doc', 'ws', 'file_ingested_doc', 'doc', 'doc', 'doc',
                   1, 1, '[]', 0.5, 0.5, 0, '2026-01-01T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO chunks_fts (chunk_id, workspace_id, path, symbols, text, summary)
           VALUES ('chk_ingested_doc', 'ws', 'docs/uploaded.md', '', 'doc', '')"""
    )
    conn.commit()
    conn.close()

    report = bulk.bulk_index(project, workspace_id="ws", db_path=db_path, force=True)

    assert report.errors == 0
    assert report.pruned_digests == 1
    assert report.pruned_files == 1
    assert report.pruned_chunks == 1
    assert report.pruned_edges == 1
    conn = sqlite3.connect(db_path)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM code_digests WHERE file_path='src/deleted.py'"
        ).fetchone()[0]
        == 0
    )
    assert conn.execute("SELECT COUNT(*) FROM files WHERE path='src/deleted.py'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE id='chk_stale'").fetchone()[0] == 0
    assert (
        conn.execute("SELECT COUNT(*) FROM files WHERE id='file_ingested_doc'").fetchone()[0] == 1
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM chunks WHERE id='chk_ingested_doc'").fetchone()[0] == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE chunk_id='chk_ingested_doc'"
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunk_id='chk_stale'").fetchone()[0]
        == 0
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM symbol_edges WHERE id='edge_stale'").fetchone()[0] == 0
    )
    conn.close()


def test_bulk_index_no_prune_stale_keeps_existing_rows(project: Path, db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO code_digests (id, workspace_id, file_path, file_sha1,
                                     language, chunk_count, symbol_count,
                                     inbound_edge_count, outbound_edge_count,
                                     purpose_short, top_symbols_json,
                                     last_indexed_at, updated_at)
           VALUES ('dig_keep', 'ws', 'src/deleted.py', 'old', 'python',
                   1, 1, 0, 0, 'old', '[]',
                   '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"""
    )
    conn.commit()
    conn.close()

    report = bulk.bulk_index(
        project,
        workspace_id="ws",
        db_path=db_path,
        force=True,
        prune_stale=False,
    )

    assert report.pruned_digests == 0
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM code_digests WHERE id='dig_keep'").fetchone()[0] == 1
    conn.close()


def test_bulk_index_no_edges_prunes_only_digests(project: Path, db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO files (id, workspace_id, path, language, content_hash,
                              size_bytes, last_indexed_at, is_archived)
           VALUES ('file_keep', 'ws', 'src/deleted.py', 'python', 'old', 10,
                   '2026-01-01T00:00:00Z', 0)"""
    )
    conn.execute(
        """INSERT INTO code_digests (id, workspace_id, file_path, file_sha1,
                                     language, chunk_count, symbol_count,
                                     inbound_edge_count, outbound_edge_count,
                                     purpose_short, top_symbols_json,
                                     last_indexed_at, updated_at)
           VALUES ('dig_delete', 'ws', 'src/deleted.py', 'old', 'python',
                   1, 1, 0, 0, 'old', '[]',
                   '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"""
    )
    conn.commit()
    conn.close()

    report = bulk.bulk_index(
        project,
        workspace_id="ws",
        db_path=db_path,
        force=True,
        with_edges=False,
    )

    assert report.pruned_digests == 1
    assert report.pruned_files == 0
    conn = sqlite3.connect(db_path)
    assert (
        conn.execute("SELECT COUNT(*) FROM code_digests WHERE id='dig_delete'").fetchone()[0] == 0
    )
    assert conn.execute("SELECT COUNT(*) FROM files WHERE id='file_keep'").fetchone()[0] == 1
    conn.close()


# ============================================================
# main()
# ============================================================


def test_main_json_output(project: Path, db_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = bulk.main(
        [
            "--project",
            str(project),
            "--workspace",
            "ws",
            "--db-path",
            str(db_path),
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["indexed"] == 3
    assert payload["workspace_id"] == "ws"


def test_main_human_output(project: Path, db_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = bulk.main(["--project", str(project), "--workspace", "ws", "--db-path", str(db_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Bulk index report" in out
    assert "indexed           = 3" in out


def test_main_missing_project_returns_two(tmp_path: Path) -> None:
    rc = bulk.main(
        [
            "--project",
            str(tmp_path / "nope"),
            "--workspace",
            "ws",
            "--db-path",
            str(tmp_path / "v.db"),
        ]
    )
    assert rc == 2


def test_main_force_flag(project: Path, db_path: Path, capsys: pytest.CaptureFixture) -> None:
    bulk.main(["--project", str(project), "--workspace", "ws", "--db-path", str(db_path), "--json"])
    capsys.readouterr()
    rc = bulk.main(
        [
            "--project",
            str(project),
            "--workspace",
            "ws",
            "--db-path",
            str(db_path),
            "--force",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["indexed"] == 3  # forced re-index
    assert payload["skipped_unchanged"] == 0


def test_main_backup_first_records_db_backup(
    project: Path, db_path: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = bulk.main(
        [
            "--project",
            str(project),
            "--workspace",
            "ws",
            "--db-path",
            str(db_path),
            "--backup-first",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    backup_path = Path(payload["backups"]["db"])
    assert backup_path.exists()
    assert backup_path.name.startswith("memory_before_bulk_code_index_")
