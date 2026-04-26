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
        "/memory/update_task_state",
        json={
            "workspace_id": "default",
            "task_id": "phase-3",
            "goal": "ship Phase 3",
            "status": "in_progress",
            "next_action": "wire LLM extractor",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"


def test_task_state_in_get_context(client: TestClient) -> None:
    client.post(
        "/memory/update_task_state",
        json={
            "workspace_id": "default",
            "task_id": "phase-3",
            "goal": "ship Phase 3",
            "status": "in_progress",
            "next_action": "wire decisions",
        },
    ).raise_for_status()

    response = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "default",
            "task_id": "phase-3",
            "query": "what is the next action",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "<task_state task_id=" in body["context_text"]
    assert "wire decisions" in body["context_text"]


def test_decision_appears_in_get_context(client: TestClient) -> None:
    client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "Use SQLite",
            "decision_text": "Lite memory uses SQLite as source of record.",
        },
    ).raise_for_status()
    response = client.post(
        "/memory/get_context",
        json={"workspace_id": "default", "query": "anything"},
    )
    body = response.json()
    assert "Use SQLite" in body["context_text"]
