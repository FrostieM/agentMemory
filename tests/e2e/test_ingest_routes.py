"""End-to-end tests for /memory/ingest_episode and /memory/search."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agent_memory_lite.api.app import create_app


@pytest.fixture
def client(settings_factory) -> Iterator[TestClient]:
    settings = settings_factory()
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, text: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "workspace_id": "default",
        "task_id": "phase-1-e2e",
        "source_type": "agent_action",
        "raw_text": text,
        "trust_level": "agent_observed",
        "importance": 0.7,
    }
    payload.update(overrides)
    response = client.post("/memory/ingest_episode", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def test_ingest_then_search_finds_episode(client: TestClient) -> None:
    body = _ingest(client, "Reindexed the retrieval module after refactor.")
    chunk_id = body["chunk_id"]

    response = client.post(
        "/memory/search",
        json={"workspace_id": "default", "query": "retrieval", "mode": "fts"},
    )
    assert response.status_code == 200
    hits = response.json()["hits"]
    assert any(h["chunk_id"] == chunk_id for h in hits)


def test_ingest_redacts_secrets_before_storage(client: TestClient) -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB"
    body = _ingest(client, f"Updated CI token={secret}")
    assert secret not in body["redacted_text"]
    assert body["redacted_kinds"], "expected at least one redacted kind"


def test_search_unknown_mode_rejected(client: TestClient) -> None:
    response = client.post(
        "/memory/search",
        json={"workspace_id": "default", "query": "x", "mode": "vector"},
    )
    # pydantic rejects the unknown literal at validation time -> 422
    assert response.status_code == 422


def test_ingest_validates_empty_text(client: TestClient) -> None:
    response = client.post(
        "/memory/ingest_episode",
        json={
            "workspace_id": "default",
            "source_type": "agent_action",
            "raw_text": "",
        },
    )
    assert response.status_code == 422
