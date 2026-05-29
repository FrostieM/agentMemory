"""Auto-capture of findings into the issue store (plan step 9.4)."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.maintenance.issue_capture import capture_findings_as_issues


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    db = tmp_path / "m.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    apply_migrations(c, MIGRATION_DIR)
    yield c
    c.close()


def _rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM issues ORDER BY created_at").fetchall()


def test_warning_finding_becomes_open_major_issue(conn: sqlite3.Connection) -> None:
    findings = [
        {
            "kind": "weak_decision_provenance",
            "severity": "warning",
            "summary": "Important active decision lacks rationale",
            "target_type": "decision",
            "target_id": "dec_x",
            "details": {"importance": 0.9},
        }
    ]
    n = capture_findings_as_issues(conn, workspace_id="w", findings=findings, source="hygiene")
    assert n == 1
    (row,) = _rows(conn)
    assert row["severity"] == "major"
    assert row["status"] == "open"
    assert row["source"] == "hygiene"
    assert "decision:dec_x" in row["evidence_json"]


def test_info_findings_are_filtered_by_default_floor(conn: sqlite3.Connection) -> None:
    findings = [{"severity": "info", "summary": "minor note", "kind": "noise"}]
    assert capture_findings_as_issues(conn, workspace_id="w", findings=findings) == 0
    assert _rows(conn) == []


def test_min_severity_minor_captures_everything(conn: sqlite3.Connection) -> None:
    findings = [{"severity": "info", "summary": "minor note", "kind": "noise"}]
    n = capture_findings_as_issues(conn, workspace_id="w", findings=findings, min_severity="minor")
    assert n == 1
    assert _rows(conn)[0]["severity"] == "minor"


def test_duck_typed_object_findings_work(conn: sqlite3.Connection) -> None:
    finding = SimpleNamespace(
        severity="critical",
        summary="integrity check failed",
        kind="sqlite",
        target_type="db",
        target_id="memory.db",
        details={"foreign_key_violations": 90},
    )
    n = capture_findings_as_issues(conn, workspace_id="w", findings=[finding], source="integrity")
    assert n == 1
    assert _rows(conn)[0]["severity"] == "critical"


def test_recurring_finding_is_deduped(conn: sqlite3.Connection) -> None:
    findings = [{"severity": "warning", "summary": "same finding", "kind": "k"}]
    capture_findings_as_issues(conn, workspace_id="w", findings=findings)
    capture_findings_as_issues(conn, workspace_id="w", findings=findings)  # re-run audit
    assert len(_rows(conn)) == 1  # signature dedup keeps one open row


def test_empty_findings_is_noop(conn: sqlite3.Connection) -> None:
    assert capture_findings_as_issues(conn, workspace_id="w", findings=[]) == 0
    assert capture_findings_as_issues(conn, workspace_id="w", findings=None) == 0
