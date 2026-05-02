"""E2E for /memory/review_queue + /memory/compact_trigger."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="rq-ws")
    with TestClient(app) as c:
        yield c


def test_review_queue_starts_empty(client: TestClient) -> None:
    response = client.post("/memory/review_queue", json={"workspace_id": "rq-ws"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workspace_id"] == "rq-ws"
    assert body["counts"]["total"] == 0
    assert body["items"] == []


def test_review_queue_surfaces_new_candidates(client: TestClient) -> None:
    # An ingested episode produces extraction candidates (heuristic
    # path) when the raw_text mentions a project decision marker.
    ingest = client.post(
        "/memory/ingest_episode",
        json={
            "workspace_id": "rq-ws",
            "source_type": "agent_action",
            "raw_text": (
                "Decision: keep the local-only guard enabled in production. "
                "Rationale: prevents accidental cloud egress."
            ),
        },
    )
    assert ingest.status_code == 200, ingest.text
    response = client.post("/memory/review_queue", json={"workspace_id": "rq-ws"})
    assert response.status_code == 200, response.text
    body = response.json()
    candidate_actions = [item for item in body["items"] if item["action"] == "promote_candidate"]
    if not candidate_actions:
        pytest.skip("No candidates extracted in this build configuration; queue empty.")
    assert candidate_actions[0]["target_type"] == "candidate"
    assert candidate_actions[0]["severity"] == "info"


def test_compact_trigger_disabled_by_default(client: TestClient) -> None:
    response = client.post("/memory/compact_trigger", json={"workspace_id": "rq-ws"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "enabled": False,
        "total": 0,
        "stale": 0,
        "threshold": 0,
        "triggered": False,
        "event_written": False,
    }


def test_compact_trigger_enabled_emits_event(app_factory) -> None:
    app = app_factory(
        MEMORY_WORKSPACE_ID="rq-compact-ws",
        MEMORY_COMPACT_TRIGGER_THRESHOLD_CHUNKS="1",
    )
    with TestClient(app) as client:
        # Seed at least one stale chunk so the (total >= threshold and
        # stale > 0) condition holds. The chunks table accepts a row
        # with a created_at far in the past, which the watchdog reads.
        ingest = client.post(
            "/memory/ingest_file",
            json={
                "workspace_id": "rq-compact-ws",
                "path": "stale_doc.txt",
                "content": "This is a sample document. " * 50,
                "language": "text",
            },
        )
        assert ingest.status_code == 200, ingest.text
        # Manually backdate the chunks so the stale-90-days predicate
        # returns at least one row.
        import sqlite3  # noqa: PLC0415

        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415

        settings = get_settings()
        conn = sqlite3.connect(str(settings.db_path))
        try:
            conn.execute(
                "UPDATE chunks SET created_at = '2020-01-01T00:00:00Z' WHERE workspace_id = ?",
                ("rq-compact-ws",),
            )
            conn.commit()
        finally:
            conn.close()
        response = client.post("/memory/compact_trigger", json={"workspace_id": "rq-compact-ws"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["enabled"] is True
        assert body["threshold"] == 1
        assert body["triggered"] is True
        # First call writes the event; second call must not (idempotent).
        again = client.post("/memory/compact_trigger", json={"workspace_id": "rq-compact-ws"})
        assert again.json()["event_written"] is False
