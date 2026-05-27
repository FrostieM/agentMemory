from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_initial_update(client: TestClient) -> None:
    response = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "task",
            "payload": {
                "task_id": "phase-3",
                "goal": "ship Phase 3",
                "status": "in_progress",
                "next_action": "wire LLM extractor",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "in_progress"


def test_task_state_in_compact_search(client: TestClient) -> None:
    client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "task",
            "payload": {
                "task_id": "phase-3",
                "goal": "ship Phase 3",
                "status": "in_progress",
                "next_action": "wire decisions",
            },
        },
    ).raise_for_status()

    response = client.post(
        "/memory/search",
        json={
            "workspace_id": "default",
            "query": "what is the next action",
            "kinds": ["task"],
            "limit": 5,
        },
    )
    assert response.status_code == 200
    assert "wire decisions" in str(response.json()["data"])


def test_decision_appears_in_compact_search(client: TestClient) -> None:
    client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "decision",
            "payload": {
                "title": "Use SQLite",
                "decision_text": "Lite memory uses SQLite as source of record.",
            },
        },
    ).raise_for_status()
    response = client.post(
        "/memory/search",
        json={"workspace_id": "default", "query": "SQLite", "kinds": ["decision"], "limit": 5},
    )
    body = response.json()
    assert "Use SQLite" in str(body["data"])
