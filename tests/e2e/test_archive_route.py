"""E2E for POST /memory/archive — universal soft-delete dispatcher."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="project-a")
    with TestClient(app) as c:
        yield c


def _ingest_episode(client: TestClient, text: str) -> dict:
    response = client.post(
        "/memory/ingest_episode",
        json={
            "workspace_id": "project-a",
            "source_type": "agent_action",
            "raw_text": text,
            "trust_level": "agent_observed",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_archive_chunk_marks_canonical_row(client: TestClient) -> None:
    ingested = _ingest_episode(
        client, "Heap watchdog triggered when allocator could not free pages"
    )
    chunk_id = ingested["chunk_id"]
    assert chunk_id

    # Sanity: search returns the chunk un-archived.
    pre = client.post(
        "/memory/search",
        json={"workspace_id": "project-a", "query": "heap watchdog", "limit": 5},
    )
    assert pre.status_code == 200
    pre_body = pre.json()
    assert pre_body["ok"] is True
    pre_hits = pre_body["data"]
    assert any(h["projection"]["id"] == chunk_id for h in pre_hits)

    # Archive the chunk.
    archived = client.post(
        "/memory/archive",
        json={"workspace_id": "project-a", "kind": "chunk", "id": chunk_id},
    )
    assert archived.status_code == 200, archived.text
    archived_body = archived.json()
    assert archived_body["ok"] is True
    assert archived_body["data"]["id"] == chunk_id
    assert archived_body["data"]["kind"] == "chunk"

    archived_get = client.get(
        "/memory/get",
        params={
            "workspace_id": "project-a",
            "kind": "chunk",
            "id": chunk_id,
            "fields": "is_archived",
        },
    )
    assert archived_get.status_code == 200
    archived_data = archived_get.json()["data"]
    assert archived_data["is_archived"] == 1

    # Archived rows stay fetchable by id, but disappear from discovery.
    post = client.post(
        "/memory/search",
        json={"workspace_id": "project-a", "query": "heap watchdog", "limit": 5},
    )
    assert post.status_code == 200
    post_body = post.json()
    assert post_body["ok"] is True
    post_hits = post_body["data"]
    assert not any(h["projection"]["id"] == chunk_id for h in post_hits)


def test_archive_can_be_reversed_with_memory_edit(client: TestClient) -> None:
    ingested = _ingest_episode(client, "QA archive round-trip episode")
    chunk_id = ingested["chunk_id"]

    archived = client.post(
        "/memory/archive",
        json={"workspace_id": "project-a", "kind": "chunk", "id": chunk_id},
    )
    assert archived.json()["ok"] is True

    restored = client.post(
        "/memory/edit",
        json={
            "workspace_id": "project-a",
            "kind": "chunk",
            "id": chunk_id,
            "fields": {"is_archived": 0},
        },
    )
    assert restored.status_code == 200
    restored_body = restored.json()
    assert restored_body["ok"] is True
    assert restored_body["data"]["id"] == chunk_id

    get_response = client.get(
        "/memory/get",
        params={
            "workspace_id": "project-a",
            "kind": "chunk",
            "id": chunk_id,
            "fields": "is_archived",
        },
    )
    assert get_response.status_code == 200
    assert get_response.json()["data"]["is_archived"] == 0


def test_archive_unsupported_kind_returns_400(client: TestClient) -> None:
    response = client.post(
        "/memory/archive",
        json={"workspace_id": "project-a", "kind": "bogus", "id": "x"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found_or_unsupported"
    assert "cannot archive bogus:x" in body["error"]["message"]


def test_archive_missing_target_returns_found_false(client: TestClient) -> None:
    response = client.post(
        "/memory/archive",
        json={"workspace_id": "project-a", "kind": "chunk", "id": "chk_does_not_exist"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found_or_unsupported"
    assert "cannot archive chunk:chk_does_not_exist" in body["error"]["message"]
