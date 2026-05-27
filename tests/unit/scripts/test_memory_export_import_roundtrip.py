"""Round-trip test for memory_export_to_json + memory_import_from_json (Phase 2.6).

Locks: workspace knowledge survives a JSON export → fresh-DB import without
loss; pinned/active flags preserved; foreign workspace_id refused unless
--allow-rename; dry-run writes nothing.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from agent_memory_lite.db.connection import open_connection
from agent_memory_lite.db.migrations import apply_migrations


def _load_script(name: str) -> object:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def export_script() -> object:
    return _load_script("memory_export_to_json")


@pytest.fixture
def import_script() -> object:
    return _load_script("memory_import_from_json")


def _seed_minimal_db(tmp_path: Path, workspace_id: str = "alpha") -> Path:
    db_path = tmp_path / "src.db"
    conn = open_connection(db_path)
    apply_migrations(conn)
    now = "2026-05-10T00:00:00+00:00"
    # Seed one decision (pinned) + one behavior (pinned).
    # Schema-shape is captured at the time of writing; the export side
    # reads SELECT *, so adding a new column wouldn't break the export
    # — but a missing column in the seed would. Keep this matched with
    # the migrations.
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale, status,
            supersedes_decision_id, source_episode_id, confidence,
            importance, valid_from, valid_to, created_at, updated_at,
            pinned, feedback_ewma, last_retrieved_at, references_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "dec_test_1",
            workspace_id,
            "Pinned test decision",
            "we will use SQLite",
            "small footprint",
            "active",
            None,
            None,
            0.95,
            0.9,
            now,
            None,
            now,
            now,
            1,
            0.0,
            None,
            None,
        ),
    )
    conn.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, scope, priority, rule, rationale,
            applies_to_json, conflict_policy, source_episode_id, confidence,
            active, created_at, updated_at, source_type, source_id,
            reviewed_by, reviewed_at, expires_at, last_applied_at,
            application_count, conflict_group, pinned)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "beh_test_1",
            workspace_id,
            "test-pinned-rule",
            "operating_rule",
            "workspace",
            "user_preference",
            "always do X before Y",
            "test rationale",
            '["context"]',
            "current_user_wins",
            None,
            0.9,
            1,
            now,
            now,
            "manual",
            None,
            None,
            None,
            None,
            None,
            0,
            None,
            1,
        ),
    )
    conn.commit()
    conn.close()
    return db_path


def _fresh_target_db(tmp_path: Path) -> Path:
    target = tmp_path / "dst.db"
    conn = open_connection(target)
    apply_migrations(conn)
    conn.close()
    return target


def test_export_then_import_preserves_rows_and_flags(
    tmp_path: Path, export_script: object, import_script: object
) -> None:
    src_db = _seed_minimal_db(tmp_path)
    dst_db = _fresh_target_db(tmp_path)
    out_dir = tmp_path / "sync"

    rc = export_script.main(
        [  # type: ignore[attr-defined]
            "--workspace",
            "alpha",
            "--db-path",
            str(src_db),
            "--out",
            str(out_dir),
        ]
    )
    assert rc == 0
    assert (out_dir / "alpha" / "decisions.json").exists()
    assert (out_dir / "alpha" / "behaviors.json").exists()

    rc = import_script.main(
        [  # type: ignore[attr-defined]
            "--workspace",
            "alpha",
            "--db-path",
            str(dst_db),
            "--in",
            str(out_dir),
            "--apply",
        ]
    )
    assert rc == 0

    conn = sqlite3.connect(dst_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, title, pinned FROM decisions WHERE workspace_id='alpha'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == "dec_test_1"
    assert rows[0]["pinned"] == 1

    bis = conn.execute(
        "SELECT name, pinned, active FROM behaviors WHERE workspace_id='alpha'"
    ).fetchall()
    assert len(bis) == 1
    assert bis[0]["name"] == "test-pinned-rule"
    assert bis[0]["pinned"] == 1
    assert bis[0]["active"] == 1
    conn.close()


def test_dry_run_does_not_mutate(
    tmp_path: Path, export_script: object, import_script: object
) -> None:
    src_db = _seed_minimal_db(tmp_path)
    dst_db = _fresh_target_db(tmp_path)
    out_dir = tmp_path / "sync"
    export_script.main(
        [  # type: ignore[attr-defined]
            "--workspace",
            "alpha",
            "--db-path",
            str(src_db),
            "--out",
            str(out_dir),
        ]
    )

    # No --apply → dry-run.
    rc = import_script.main(
        [  # type: ignore[attr-defined]
            "--workspace",
            "alpha",
            "--db-path",
            str(dst_db),
            "--in",
            str(out_dir),
        ]
    )
    assert rc == 0

    conn = sqlite3.connect(dst_db)
    n = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert n == 0, "dry-run must not write rows"
    n = conn.execute("SELECT COUNT(*) FROM behaviors").fetchone()[0]
    assert n == 0
    conn.close()


def test_foreign_workspace_refused_without_rename(
    tmp_path: Path,
    export_script: object,
    import_script: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src_db = _seed_minimal_db(tmp_path, workspace_id="alpha")
    dst_db = _fresh_target_db(tmp_path)
    out_dir = tmp_path / "sync"
    export_script.main(
        [  # type: ignore[attr-defined]
            "--workspace",
            "alpha",
            "--db-path",
            str(src_db),
            "--out",
            str(out_dir),
        ]
    )

    # Try to import alpha's dump into a different target workspace name.
    # Without --allow-rename the script must refuse — even though we
    # explicitly point --source-workspace at the alpha subdir.
    with pytest.raises(SystemExit) as exc_info:
        import_script.main(
            [  # type: ignore[attr-defined]
                "--workspace",
                "beta",
                "--source-workspace",
                "alpha",
                "--db-path",
                str(dst_db),
                "--in",
                str(out_dir),
                "--apply",
            ]
        )
    assert exc_info.value.code == 3
    err = capsys.readouterr().err
    assert "refuse" in err
    assert "--allow-rename" in err


def test_allow_rename_rewrites_workspace_id(
    tmp_path: Path,
    export_script: object,
    import_script: object,
) -> None:
    src_db = _seed_minimal_db(tmp_path, workspace_id="alpha")
    dst_db = _fresh_target_db(tmp_path)
    out_dir = tmp_path / "sync"
    export_script.main(
        [  # type: ignore[attr-defined]
            "--workspace",
            "alpha",
            "--db-path",
            str(src_db),
            "--out",
            str(out_dir),
        ]
    )

    rc = import_script.main(
        [  # type: ignore[attr-defined]
            "--workspace",
            "beta",
            "--source-workspace",
            "alpha",
            "--db-path",
            str(dst_db),
            "--in",
            str(out_dir),
            "--apply",
            "--allow-rename",
        ]
    )
    assert rc == 0

    conn = sqlite3.connect(dst_db)
    n_alpha = conn.execute("SELECT COUNT(*) FROM decisions WHERE workspace_id='alpha'").fetchone()[
        0
    ]
    n_beta = conn.execute("SELECT COUNT(*) FROM decisions WHERE workspace_id='beta'").fetchone()[0]
    assert n_alpha == 0
    assert n_beta == 1
    conn.close()


def test_idempotent_double_import(
    tmp_path: Path,
    export_script: object,
    import_script: object,
) -> None:
    src_db = _seed_minimal_db(tmp_path)
    dst_db = _fresh_target_db(tmp_path)
    out_dir = tmp_path / "sync"
    export_script.main(
        [  # type: ignore[attr-defined]
            "--workspace",
            "alpha",
            "--db-path",
            str(src_db),
            "--out",
            str(out_dir),
        ]
    )

    for _ in range(2):
        rc = import_script.main(
            [  # type: ignore[attr-defined]
                "--workspace",
                "alpha",
                "--db-path",
                str(dst_db),
                "--in",
                str(out_dir),
                "--apply",
            ]
        )
        assert rc == 0

    conn = sqlite3.connect(dst_db)
    n = conn.execute("SELECT COUNT(*) FROM decisions WHERE workspace_id='alpha'").fetchone()[0]
    assert n == 1, "double import must not duplicate; INSERT OR REPLACE keys on id"
    conn.close()
