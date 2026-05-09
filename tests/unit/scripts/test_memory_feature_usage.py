"""Tests for scripts/memory_feature_usage.py (Phase 2.1)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def _load_script() -> object:
    """Import the script-as-module since it lives outside the src tree."""
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "memory_feature_usage.py"
    spec = importlib.util.spec_from_file_location("memory_feature_usage", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["memory_feature_usage"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script() -> object:
    return _load_script()


def _seed_db(db_path: Path, *, workspace_id: str, rows: list[tuple[str, str]]) -> None:
    """Create a minimal audit_log table and insert rows of (action, created_at)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE audit_log (
            id           TEXT PRIMARY KEY,
            workspace_id TEXT,
            action       TEXT,
            target_type  TEXT,
            target_id    TEXT,
            created_at   TEXT
        )
        """
    )
    conn.executemany(
        """INSERT INTO audit_log (id, workspace_id, action, created_at)
           VALUES (?, ?, ?, ?)""",
        [
            (f"a{i}", workspace_id, action, created_at)
            for i, (action, created_at) in enumerate(rows)
        ],
    )
    conn.commit()
    conn.close()


def test_action_counts_filters_by_workspace_and_window(tmp_path: Path, script: object) -> None:
    """Only rows in the chosen workspace and inside [since, now] are counted."""
    db = tmp_path / "memory.db"
    now = datetime.now(UTC)
    inside = (now - timedelta(days=10)).isoformat()
    outside = (now - timedelta(days=120)).isoformat()
    _seed_db(
        db,
        workspace_id="alpha",
        rows=[
            ("ingest_episode", inside),
            ("ingest_episode", inside),
            ("write_decision", inside),
            ("write_decision", outside),
        ],
    )
    # Add a row in a different workspace inside the window — must be ignored.
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO audit_log (id, workspace_id, action, created_at) VALUES (?, ?, ?, ?)",
        ("x", "beta", "ingest_episode", inside),
    )
    conn.commit()
    conn.close()

    since_iso = (now - timedelta(days=60)).isoformat()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    counts = script._action_counts(conn, "alpha", since_iso)  # type: ignore[attr-defined]
    conn.close()

    assert counts == {"ingest_episode": 2, "write_decision": 1}


def test_format_table_marks_retire_and_light_usage(script: object) -> None:
    """The CLI table marks <5 as retire candidate and <20 as light usage."""
    counts = {"hot_action": 200, "warm_action": 12, "cold_action": 3}
    out = script._format_table(counts, "test")  # type: ignore[attr-defined]
    assert "[retire candidate]" in out
    assert "[light usage]" in out
    # Hot actions get no marker.
    hot_line = next(line for line in out.splitlines() if "hot_action" in line)
    assert "[retire" not in hot_line
    assert "[light" not in hot_line


def test_format_table_handles_empty_window(script: object) -> None:
    out = script._format_table({}, "empty")  # type: ignore[attr-defined]
    assert "no audit_log entries in window" in out


def test_main_emits_json_when_flag_set(
    tmp_path: Path, script: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` produces a parseable payload with workspaces + rollup keys."""
    db = tmp_path / "memory.db"
    now = datetime.now(UTC)
    _seed_db(
        db,
        workspace_id="alpha",
        rows=[
            ("ingest_episode", (now - timedelta(days=5)).isoformat()),
            ("ingest_episode", (now - timedelta(days=5)).isoformat()),
            ("write_decision", (now - timedelta(days=5)).isoformat()),
        ],
    )

    rc = script.main(
        [  # type: ignore[attr-defined]
            "--workspace",
            "alpha",
            "--db-path",
            str(db),
            "--days",
            "30",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["days"] == 30
    assert "alpha" in payload["workspaces"]
    assert payload["workspaces"]["alpha"]["ingest_episode"] == 2
    assert payload["workspaces"]["alpha"]["write_decision"] == 1
    # rollup must equal per-workspace sum when only one workspace was scanned.
    assert payload["rollup"]["ingest_episode"] == 2


def test_main_include_action_filter(
    tmp_path: Path, script: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--include-action` keeps only actions whose name contains the substring."""
    db = tmp_path / "memory.db"
    now = datetime.now(UTC)
    iso = (now - timedelta(days=5)).isoformat()
    _seed_db(
        db,
        workspace_id="alpha",
        rows=[
            ("ingest_episode", iso),
            ("ingest_file", iso),
            ("write_decision", iso),
        ],
    )

    rc = script.main(
        [  # type: ignore[attr-defined]
            "--workspace",
            "alpha",
            "--db-path",
            str(db),
            "--days",
            "30",
            "--include-action",
            "ingest_",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["workspaces"]["alpha"]) == {"ingest_episode", "ingest_file"}
    assert "write_decision" not in payload["workspaces"]["alpha"]
