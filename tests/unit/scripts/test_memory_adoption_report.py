"""Tests for scripts/memory_adoption_report.py (Phase 1.6).

Locks the three discipline ratios computed from audit_log:
* link_after_write — link_capability per linkable mutation
* candidate_triage — promote/reject per candidate write
* decision_provenance — fraction of decisions with source_episode_id

Audit_log captures only mutations; read-side adoption is intentionally
OUT of scope.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_memory_lite.db.connection import open_connection
from agent_memory_lite.db.migrations import apply_migrations


def _load_script() -> object:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "memory_adoption_report.py"
    spec = importlib.util.spec_from_file_location("memory_adoption_report", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["memory_adoption_report"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script() -> object:
    return _load_script()


def _seed_audit(
    db_path: Path,
    *,
    workspace_id: str,
    rows: list[tuple[str, str | None, str]],
) -> None:
    """Append (action, agent_id, created_at) audit_log rows."""
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO audit_log (id, workspace_id, action, target_type, target_id, "
        "created_at, agent_id) VALUES (?, ?, ?, '_test_', '_t_', ?, ?)",
        [
            (f"a{i}", workspace_id, action, created_at, agent_id)
            for i, (action, agent_id, created_at) in enumerate(rows)
        ],
    )
    conn.commit()
    conn.close()


def _seed_decision(
    db_path: Path,
    *,
    decision_id: str,
    workspace_id: str,
    created_at: str,
    source_episode_id: str | None,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale, status,
            supersedes_decision_id, source_episode_id, confidence, importance,
            valid_from, valid_to, created_at, updated_at, pinned,
            feedback_ewma, last_retrieved_at, references_json)
           VALUES (?, ?, 'T', 't', 'r', 'active', NULL, ?, 0.9, 0.8, ?, NULL,
                   ?, ?, 0, 0.0, NULL, NULL)""",
        (
            decision_id,
            workspace_id,
            source_episode_id,
            created_at,
            created_at,
            created_at,
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "src.db"
    conn = open_connection(db_path)
    apply_migrations(conn)
    conn.close()
    return db_path


def test_bucket_thresholds(script: object) -> None:
    """Red < 0.30, amber 0.30..0.59, green >= 0.60."""
    assert script._bucket(0.0) == "red"  # type: ignore[attr-defined]
    assert script._bucket(0.29) == "red"  # type: ignore[attr-defined]
    assert script._bucket(0.30) == "amber"  # type: ignore[attr-defined]
    assert script._bucket(0.59) == "amber"  # type: ignore[attr-defined]
    assert script._bucket(0.60) == "green"  # type: ignore[attr-defined]
    assert script._bucket(1.5) == "green"  # type: ignore[attr-defined]
    assert script._bucket(None) == "n/a"  # type: ignore[attr-defined]


def test_link_after_write_ratio_green(db: Path, script: object) -> None:
    """1 link per 1 write = 1.0 green."""
    now = datetime.now(UTC)
    iso = (now - timedelta(days=1)).isoformat()
    _seed_audit(
        db,
        workspace_id="alpha",
        rows=[
            ("write_decision", "claude", iso),
            ("link_capability", "claude", iso),
        ],
    )
    rc = script.main(  # type: ignore[attr-defined]
        ["--workspace", "alpha", "--db-path", str(db), "--days", "7", "--json"]
    )
    assert rc == 0


def test_link_after_write_ratio_red(
    db: Path, script: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """3 writes / 0 links = 0.0 red. JSON shape carries the raw counts."""
    iso = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _seed_audit(
        db,
        workspace_id="alpha",
        rows=[
            ("write_decision", "claude", iso),
            ("write_theory", "claude", iso),
            ("write_experiment", "claude", iso),
        ],
    )
    rc = script.main(  # type: ignore[attr-defined]
        ["--workspace", "alpha", "--db-path", str(db), "--days", "7", "--json"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    ratios = payload["workspaces"]["alpha"]["aggregate"]["ratios"]
    assert ratios["link_after_write"]["ratio"] == 0.0
    assert ratios["link_after_write"]["bucket"] == "red"
    assert ratios["link_after_write"]["links"] == 0
    assert ratios["link_after_write"]["writes"] == 3


def test_candidate_triage_ratio(
    db: Path, script: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """promote+reject / candidates_written = triage ratio."""
    iso = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _seed_audit(
        db,
        workspace_id="alpha",
        rows=[
            ("write_memory_candidate", "claude", iso),
            ("write_memory_candidate", "claude", iso),
            ("write_memory_candidate", "claude", iso),
            ("promote_candidate", "operator", iso),
            ("reject_candidate", "operator", iso),
        ],
    )
    rc = script.main(  # type: ignore[attr-defined]
        ["--workspace", "alpha", "--db-path", str(db), "--days", "7", "--json"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    ratios = payload["workspaces"]["alpha"]["aggregate"]["ratios"]
    triage = ratios["candidate_triage"]
    assert triage["candidates"] == 3
    assert triage["triages"] == 2
    assert triage["ratio"] == pytest.approx(2 / 3)
    assert triage["bucket"] == "green"


def test_decision_provenance_ratio(
    db: Path, script: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """Decisions with source_episode_id / total decisions in window."""
    iso = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _seed_decision(
        db, decision_id="d1", workspace_id="alpha", created_at=iso, source_episode_id="ep_1"
    )
    _seed_decision(
        db, decision_id="d2", workspace_id="alpha", created_at=iso, source_episode_id=None
    )
    _seed_decision(
        db, decision_id="d3", workspace_id="alpha", created_at=iso, source_episode_id="ep_3"
    )
    _seed_audit(
        db,
        workspace_id="alpha",
        rows=[("write_decision", "claude", iso)] * 3,
    )

    rc = script.main(  # type: ignore[attr-defined]
        ["--workspace", "alpha", "--db-path", str(db), "--days", "7", "--json"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    prov = payload["workspaces"]["alpha"]["aggregate"]["ratios"]["decision_provenance"]
    assert prov["with_source"] == 2
    assert prov["total_decisions"] == 3
    assert prov["ratio"] == pytest.approx(2 / 3)
    assert prov["bucket"] == "green"


def test_window_filter_drops_old_rows(
    db: Path, script: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rows older than --days must be excluded from every ratio."""
    now = datetime.now(UTC)
    inside = (now - timedelta(days=1)).isoformat()
    outside = (now - timedelta(days=120)).isoformat()
    _seed_audit(
        db,
        workspace_id="alpha",
        rows=[
            ("write_decision", "claude", inside),
            ("link_capability", "claude", inside),
            # Outside-window rows must NOT be counted.
            ("write_decision", "claude", outside),
            ("write_decision", "claude", outside),
        ],
    )
    rc = script.main(  # type: ignore[attr-defined]
        ["--workspace", "alpha", "--db-path", str(db), "--days", "30", "--json"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    link = payload["workspaces"]["alpha"]["aggregate"]["ratios"]["link_after_write"]
    assert link["writes"] == 1
    assert link["links"] == 1
    assert link["ratio"] == 1.0


def test_by_agent_breakdown(db: Path, script: object, capsys: pytest.CaptureFixture[str]) -> None:
    """--by-agent splits ratios per agent_id from audit_log."""
    iso = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _seed_audit(
        db,
        workspace_id="alpha",
        rows=[
            ("write_decision", "claude", iso),
            ("write_decision", "claude", iso),
            ("link_capability", "claude", iso),
            ("write_decision", "codex", iso),
            ("link_capability", "codex", iso),
            ("link_capability", "codex", iso),
        ],
    )
    rc = script.main(  # type: ignore[attr-defined]
        [
            "--workspace",
            "alpha",
            "--db-path",
            str(db),
            "--days",
            "7",
            "--by-agent",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    agents = payload["workspaces"]["alpha"]["by_agent"]
    assert agents["claude"]["ratios"]["link_after_write"]["ratio"] == 0.5
    assert agents["codex"]["ratios"]["link_after_write"]["ratio"] == 2.0
