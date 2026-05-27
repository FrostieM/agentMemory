from __future__ import annotations

from fastapi.testclient import TestClient

from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.models.enums import MaintenanceEventStatus, MaintenanceSeverity
from agent_memory_lite.repositories.maintenance_repo import insert_maintenance_event_row
from agent_memory_lite.utils.time import iso_now


def test_list_and_resolve_maintenance_event(app_factory, tmp_db_path) -> None:
    app = app_factory(MEMORY_WORKSPACE_ID="project-a")
    conn = open_connection(tmp_db_path)
    try:
        insert_maintenance_event_row(
            conn,
            event_id="me_test",
            workspace_id="project-a",
            kind="vector_upsert_failed",
            severity=MaintenanceSeverity.ERROR,
            status=MaintenanceEventStatus.OPEN,
            summary="Vector upsert failed after SQLite commit.",
            details={"chunk_id": "chk_test"},
            source_episode_id=None,
            target_type="chunk",
            target_id="chk_test",
            created_at=iso_now(),
        )
        conn.commit()
    finally:
        close_connection(conn)

    with TestClient(app) as client:
        listed = client.post(
            "/memory/maintenance_events",
            json={"workspace_id": "project-a", "statuses": ["open"]},
        )
        assert listed.status_code == 200, listed.text
        events = listed.json()["events"]
        assert len(events) == 1
        assert events[0]["event_id"] == "me_test"

        resolved = client.post(
            "/memory/resolve_maintenance_event",
            json={"event_id": "me_test", "status": "resolved"},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["status"] == "resolved"
        assert resolved.json()["resolved_at"]
