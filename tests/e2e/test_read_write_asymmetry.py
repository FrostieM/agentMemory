"""Verify the asymmetric isolation contract."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def project_a_client(app_factory) -> Iterator[TestClient]:
    app = app_factory(
        MEMORY_WORKSPACE_ID="project-a",
        MEMORY_FORBID_DEFAULT_WORKSPACE="true",
        MEMORY_STRICT_WORKSPACE_ISOLATION="true",
    )
    with TestClient(app) as c:
        yield c


def test_read_own_workspace_succeeds(project_a_client: TestClient) -> None:
    response = project_a_client.get(
        "/memory/brief",
        params={"workspace_id": "project-a", "task": "anything", "max_tokens": 500},
    )
    assert response.status_code == 200, response.text


def test_read_foreign_workspace_succeeds(project_a_client: TestClient) -> None:
    response = project_a_client.get(
        "/memory/brief",
        params={"workspace_id": "project-b", "task": "anything", "max_tokens": 500},
    )
    assert response.status_code == 200, response.text


def test_search_foreign_workspace_succeeds(project_a_client: TestClient) -> None:
    response = project_a_client.post(
        "/memory/search",
        json={"workspace_id": "project-b", "query": "x", "limit": 5},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True


def test_write_own_workspace_succeeds(project_a_client: TestClient) -> None:
    response = project_a_client.post(
        "/memory/write",
        json={
            "workspace_id": "project-a",
            "kind": "episode",
            "payload": {
                "source_type": "agent_action",
                "raw_text": "wrote to my own workspace",
                "trust_level": "agent_observed",
                "importance": 0.5,
            },
        },
    )
    assert response.status_code == 200, response.text


def test_write_foreign_workspace_blocked(project_a_client: TestClient) -> None:
    response = project_a_client.post(
        "/memory/write",
        json={
            "workspace_id": "project-b",
            "kind": "episode",
            "payload": {
                "source_type": "agent_action",
                "raw_text": "should be rejected",
                "trust_level": "agent_observed",
                "importance": 0.5,
            },
        },
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "STRICT_WORKSPACE_ISOLATION" in detail
    assert "writes" in detail.lower() or "block" in detail.lower()


def test_write_decision_foreign_workspace_blocked(project_a_client: TestClient) -> None:
    response = project_a_client.post(
        "/memory/write",
        json={
            "workspace_id": "project-b",
            "kind": "decision",
            "payload": {"title": "should be rejected", "decision_text": "x"},
        },
    )
    assert response.status_code == 400, response.text


def test_write_task_foreign_workspace_blocked(project_a_client: TestClient) -> None:
    response = project_a_client.post(
        "/memory/write",
        json={
            "workspace_id": "project-b",
            "kind": "task",
            "payload": {"task_id": "rejected", "goal": "x", "status": "in_progress"},
        },
    )
    assert response.status_code == 400, response.text


def test_default_workspace_rejected_for_both_read_and_write(
    project_a_client: TestClient,
) -> None:
    read = project_a_client.get(
        "/memory/brief",
        params={"workspace_id": "default", "task": "x", "max_tokens": 200},
    )
    assert read.status_code == 400

    write = project_a_client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "episode",
            "payload": {
                "source_type": "agent_action",
                "raw_text": "x",
                "trust_level": "agent_observed",
                "importance": 0.5,
            },
        },
    )
    assert write.status_code == 400
