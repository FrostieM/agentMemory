"""Verify the asymmetric isolation contract.

Under strict project mode (`MEMORY_STRICT_WORKSPACE_ISOLATION=true`):
  * READS to any registered workspace succeed (the user can explicitly ask
    the agent to look at another project's memory in any chat).
  * WRITES to any workspace other than the strict anchor are rejected
    (a project chat must not pollute another project's audit log,
    decisions, behavior instructions, etc.).

The HTTP service simulates a project chat by anchoring at one workspace
with strict + forbid-default flags.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def project_a_client(app_factory) -> Iterator[TestClient]:
    """A project chat anchored at project-a with strict isolation on."""
    app = app_factory(
        MEMORY_WORKSPACE_ID="project-a",
        MEMORY_FORBID_DEFAULT_WORKSPACE="true",
        MEMORY_STRICT_WORKSPACE_ISOLATION="true",
    )
    with TestClient(app) as c:
        yield c


def test_read_own_workspace_succeeds(project_a_client: TestClient) -> None:
    response = project_a_client.post(
        "/memory/get_context",
        json={"workspace_id": "project-a", "query": "anything", "max_tokens": 500},
    )
    assert response.status_code == 200, response.text


def test_read_foreign_workspace_succeeds(project_a_client: TestClient) -> None:
    """The user explicitly asked to look at project-b — reads are loose."""
    response = project_a_client.post(
        "/memory/get_context",
        json={"workspace_id": "project-b", "query": "anything", "max_tokens": 500},
    )
    assert response.status_code == 200, response.text


def test_search_foreign_workspace_succeeds(project_a_client: TestClient) -> None:
    response = project_a_client.post(
        "/memory/search",
        json={"workspace_id": "project-b", "query": "x", "limit": 5, "mode": "fts"},
    )
    assert response.status_code == 200, response.text


def test_write_own_workspace_succeeds(project_a_client: TestClient) -> None:
    response = project_a_client.post(
        "/memory/ingest_episode",
        json={
            "workspace_id": "project-a",
            "source_type": "agent_action",
            "raw_text": "wrote to my own workspace",
            "trust_level": "agent_observed",
            "importance": 0.5,
        },
    )
    assert response.status_code == 200, response.text


def test_write_foreign_workspace_blocked(project_a_client: TestClient) -> None:
    """A project chat must never write into another workspace, even when
    the user explicitly asks. Writes are a hard boundary."""
    response = project_a_client.post(
        "/memory/ingest_episode",
        json={
            "workspace_id": "project-b",
            "source_type": "agent_action",
            "raw_text": "should be rejected",
            "trust_level": "agent_observed",
            "importance": 0.5,
        },
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "STRICT_WORKSPACE_ISOLATION" in detail
    assert "writes" in detail.lower() or "block" in detail.lower()


def test_write_decision_foreign_workspace_blocked(project_a_client: TestClient) -> None:
    response = project_a_client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "project-b",
            "title": "should be rejected",
            "decision_text": "x",
        },
    )
    assert response.status_code == 400, response.text


def test_update_task_state_foreign_workspace_blocked(project_a_client: TestClient) -> None:
    response = project_a_client.post(
        "/memory/update_task_state",
        json={
            "workspace_id": "project-b",
            "task_id": "rejected",
            "goal": "x",
            "status": "in_progress",
        },
    )
    assert response.status_code == 400, response.text


def test_default_workspace_rejected_for_both_read_and_write(
    project_a_client: TestClient,
) -> None:
    """`forbid_default_workspace=true` is independent of read/write split."""
    read = project_a_client.post(
        "/memory/get_context",
        json={"workspace_id": "default", "query": "x", "max_tokens": 200},
    )
    assert read.status_code == 400

    write = project_a_client.post(
        "/memory/ingest_episode",
        json={
            "workspace_id": "default",
            "source_type": "agent_action",
            "raw_text": "x",
            "trust_level": "agent_observed",
            "importance": 0.5,
        },
    )
    assert write.status_code == 400
