"""v3.4 #6 hygiene action queue — HTTP route tests.

End-to-end coverage for /memory/claim_maintenance_event,
/memory/dismiss_maintenance_event, and the extended
/memory/maintenance_events with action_statuses filter.

Pairs with tests/unit/maintenance/test_action_queue_repo.py — repo
tests pin the SQL contract, these pin the wire contract."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.models.enums import MaintenanceEventStatus, MaintenanceSeverity
from agent_memory_lite.repositories.maintenance_repo import insert_maintenance_event_row
from agent_memory_lite.utils.time import iso_now


def _seed(conn_path: str, *, event_id: str, summary: str = "drift") -> None:
    conn = open_connection(conn_path)
    try:
        insert_maintenance_event_row(
            conn,
            event_id=event_id,
            workspace_id="project-a",
            kind="memory_drift_fk",
            severity=MaintenanceSeverity.WARNING,
            status=MaintenanceEventStatus.OPEN,
            summary=summary,
            details={"violations": 1},
            source_episode_id=None,
            target_type=None,
            target_id=None,
            created_at=iso_now(),
        )
        conn.commit()
    finally:
        close_connection(conn)


def test_claim_endpoint_sets_action_status(app_factory, tmp_db_path) -> None:
    app = app_factory(MEMORY_WORKSPACE_ID="project-a")
    _seed(tmp_db_path, event_id="me_claim")
    with TestClient(app) as client:
        r = client.post(
            "/memory/claim_maintenance_event",
            json={
                "event_id": "me_claim",
                "assigned_to": "osino",
                "action_notes": "investigating fk drift",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["action_status"] == "claimed"
        assert body["assigned_to"] == "osino"
        assert body["action_notes"] == "investigating fk drift"
        assert body["claimed_at"]
        # Substrate status untouched.
        assert body["status"] == "open"
        assert body["resolved_at"] is None


def test_dismiss_endpoint_sets_action_status(app_factory, tmp_db_path) -> None:
    app = app_factory(MEMORY_WORKSPACE_ID="project-a")
    _seed(tmp_db_path, event_id="me_dismiss")
    with TestClient(app) as client:
        r = client.post(
            "/memory/dismiss_maintenance_event",
            json={"event_id": "me_dismiss", "action_notes": "false positive"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["action_status"] == "dismissed"
        assert body["dismissed_at"]
        assert body["status"] == "open"


def test_claim_404_for_missing_event(app_factory, tmp_db_path) -> None:
    app = app_factory(MEMORY_WORKSPACE_ID="project-a")
    with TestClient(app) as client:
        r = client.post(
            "/memory/claim_maintenance_event",
            json={"event_id": "no_such_event", "assigned_to": "x"},
        )
        assert r.status_code == 404, r.text


def test_list_filter_by_action_status(app_factory, tmp_db_path) -> None:
    """The queue page calls list with action_statuses=['open','claimed']
    to render the working queue, then again with ['dismissed'] for the
    archive view. Both must filter independently of substrate status."""
    app = app_factory(MEMORY_WORKSPACE_ID="project-a")
    _seed(tmp_db_path, event_id="me_open", summary="still open")
    _seed(tmp_db_path, event_id="me_claimed", summary="being worked on")
    with TestClient(app) as client:
        # Claim one so the two rows have different action_status.
        r = client.post(
            "/memory/claim_maintenance_event",
            json={"event_id": "me_claimed", "assigned_to": "osino"},
        )
        assert r.status_code == 200, r.text

        only_open = client.post(
            "/memory/maintenance_events",
            json={"workspace_id": "project-a", "action_statuses": ["open"]},
        )
        assert only_open.status_code == 200, only_open.text
        ids = [e["event_id"] for e in only_open.json()["events"]]
        assert ids == ["me_open"]

        only_claimed = client.post(
            "/memory/maintenance_events",
            json={"workspace_id": "project-a", "action_statuses": ["claimed"]},
        )
        assert only_claimed.status_code == 200, only_claimed.text
        ids = [e["event_id"] for e in only_claimed.json()["events"]]
        assert ids == ["me_claimed"]


def test_resolve_endpoint_syncs_action_status(app_factory, tmp_db_path) -> None:
    """Substrate resolve path drives action_status to RESOLVED so the
    queue does not keep showing the same finding once the metric clears."""
    app = app_factory(MEMORY_WORKSPACE_ID="project-a")
    _seed(tmp_db_path, event_id="me_resolve")
    with TestClient(app) as client:
        r = client.post(
            "/memory/resolve_maintenance_event",
            json={"event_id": "me_resolve", "status": "resolved"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "resolved"
        assert body["action_status"] == "resolved"
        assert body["resolved_at"]
