from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.ingestion.task_state_writer import write_task_state
from agent_memory_lite.maintenance.integrity import run_integrity_audit
from agent_memory_lite.maintenance.workspace_doctor import run_workspace_doctor
from agent_memory_lite.models.task_state import TaskStateIn


def test_workspace_doctor_reports_foreign_rows(applied_conn: sqlite3.Connection) -> None:
    write_task_state(
        applied_conn,
        TaskStateIn(
            workspace_id="foreign",
            task_id="foreign-task",
            goal="should not live in this project DB",
            status="in_progress",
        ),
    )

    report = run_workspace_doctor(applied_conn, workspace_id="project-a")

    assert report.status == "degraded"
    assert report.counts_before["task_state"]["foreign"] == 1
    assert report.counts_before["audit_log"]["foreign"] == 1
    assert {sample.table for sample in report.samples} >= {"task_state", "audit_log"}


def test_workspace_doctor_quarantines_foreign_rows(
    applied_conn: sqlite3.Connection,
    tmp_path,
) -> None:
    write_task_state(
        applied_conn,
        TaskStateIn(
            workspace_id="foreign",
            task_id="foreign-task",
            goal="should be quarantined",
            status="in_progress",
        ),
    )
    before = run_integrity_audit(applied_conn, workspace_id="project-a")
    assert before.status == "degraded"
    quarantine_path = tmp_path / "workspace_pollution.json"

    report = run_workspace_doctor(
        applied_conn,
        workspace_id="project-a",
        quarantine=True,
        quarantine_path=quarantine_path,
    )

    assert report.status == "ok"
    assert report.quarantined_rows["task_state"] == 1
    assert report.quarantined_rows["audit_log"] == 1
    assert report.counts_after == {}
    payload = json.loads(quarantine_path.read_text(encoding="utf-8"))
    assert payload["workspace_id"] == "project-a"
    assert {row["table"] for row in payload["rows"]} >= {"task_state", "audit_log"}
    after = run_integrity_audit(applied_conn, workspace_id="project-a")
    assert after.checks["workspace_pollution"].status == "ok"


def test_workspace_doctor_leaves_default_rows_unless_requested(
    applied_conn: sqlite3.Connection,
    tmp_path,
) -> None:
    write_task_state(
        applied_conn,
        TaskStateIn(
            workspace_id="default",
            task_id="default-task",
            goal="default is inspected separately",
            status="in_progress",
        ),
    )

    report = run_workspace_doctor(
        applied_conn,
        workspace_id="project-a",
        quarantine=True,
        quarantine_path=tmp_path / "foreign_only.json",
    )
    assert report.status == "degraded"
    assert report.quarantined_rows == {}
    assert report.counts_after["task_state"]["default"] == 1
    assert (
        applied_conn.execute(
            "SELECT COUNT(*) FROM task_state WHERE workspace_id = 'default'"
        ).fetchone()[0]
        == 1
    )

    default_report = run_workspace_doctor(
        applied_conn,
        workspace_id="project-a",
        include_default=True,
        quarantine=True,
        quarantine_path=tmp_path / "including_default.json",
    )
    assert default_report.status == "ok"
    assert default_report.quarantined_rows["task_state"] == 1
