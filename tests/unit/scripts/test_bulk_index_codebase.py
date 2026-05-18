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

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "v3.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
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
