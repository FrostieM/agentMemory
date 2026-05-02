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


def test_archive_chunk_drops_from_get_context(client: TestClient) -> None:
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
    pre_hits = pre.json()["hits"]
    assert any(h["chunk_id"] == chunk_id for h in pre_hits)
    matched = next(h for h in pre_hits if h["chunk_id"] == chunk_id)
    assert matched["is_archived"] is False

    # Archive the chunk.
    archived = client.post(
        "/memory/archive",
        json={"workspace_id": "project-a", "kind": "chunk", "id": chunk_id},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json() == {
        "kind": "chunk",
        "id": chunk_id,
        "archived": True,
        "found": True,
    }

    # Search still returns the archived chunk but with the marker on.
    post = client.post(
        "/memory/search",
        json={"workspace_id": "project-a", "query": "heap watchdog", "limit": 5},
    )
    assert post.status_code == 200
    post_hits = post.json()["hits"]
    matched = next(h for h in post_hits if h["chunk_id"] == chunk_id)
    assert matched["is_archived"] is True

    # get_context (default historical=false) drops the archived chunk.
    ctx = client.post(
        "/memory/get_context",
        json={"workspace_id": "project-a", "query": "heap watchdog", "max_tokens": 1500},
    )
    assert ctx.status_code == 200
    ctx_chunk_ids = {s["id"] for s in ctx.json()["sources"]}
    assert chunk_id not in ctx_chunk_ids

    # historical=true brings the archived chunk back.
    ctx_hist = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "project-a",
            "query": "heap watchdog",
            "max_tokens": 1500,
            "historical": True,
        },
    )
    assert ctx_hist.status_code == 200
    hist_ids = {s["id"] for s in ctx_hist.json()["sources"]}
    assert chunk_id in hist_ids


def test_archive_restore_round_trip(client: TestClient) -> None:
    ingested = _ingest_episode(client, "QA archive round-trip episode")
    chunk_id = ingested["chunk_id"]

    archived = client.post(
        "/memory/archive",
        json={"workspace_id": "project-a", "kind": "chunk", "id": chunk_id},
    )
    assert archived.json()["archived"] is True

    restored = client.post(
        "/memory/archive",
        json={
            "workspace_id": "project-a",
            "kind": "chunk",
            "id": chunk_id,
            "archive": False,
        },
    )
    assert restored.status_code == 200
    assert restored.json() == {
        "kind": "chunk",
        "id": chunk_id,
        "archived": False,
        "found": True,
    }

    # After restore, chunk is once again un-archived in search.
    post = client.post(
        "/memory/search",
        json={"workspace_id": "project-a", "query": "round trip", "limit": 5},
    )
    matched = next(h for h in post.json()["hits"] if h["chunk_id"] == chunk_id)
    assert matched["is_archived"] is False


def test_archive_unsupported_kind_returns_400(client: TestClient) -> None:
    response = client.post(
        "/memory/archive",
        json={"workspace_id": "project-a", "kind": "bogus", "id": "x"},
    )
    assert response.status_code == 400
    assert "unsupported archive kind" in response.json()["detail"].lower()


def test_archive_missing_target_returns_found_false(client: TestClient) -> None:
    response = client.post(
        "/memory/archive",
        json={"workspace_id": "project-a", "kind": "chunk", "id": "chk_does_not_exist"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "kind": "chunk",
        "id": "chk_does_not_exist",
        "archived": True,
        "found": False,
    }
