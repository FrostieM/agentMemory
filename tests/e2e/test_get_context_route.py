"""End-to-end tests for /memory/get_context."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, text: str) -> dict[str, object]:
    response = client.post(
        "/memory/ingest_episode",
        json={
            "workspace_id": "default",
            "task_id": "phase-2-e2e",
            "source_type": "agent_action",
            "raw_text": text,
            "trust_level": "agent_observed",
            "importance": 0.7,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def test_get_context_returns_xml_envelope(client: TestClient) -> None:
    _ingest(client, "Phase 2 wires hybrid retrieval through LanceDB and FTS.")
    response = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "default",
            "query": "hybrid retrieval LanceDB",
            "max_tokens": 1500,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "<memory_context>" in body["context_text"]
    assert "<retrieved_chunks>" in body["context_text"]


def test_get_context_surfaces_recent_episode(client: TestClient) -> None:
    ingested = _ingest(client, "Implemented the FTS+vector RRF fusion module.")
    chunk_id = ingested["chunk_id"]

    response = client.post(
        "/memory/get_context",
        json={"workspace_id": "default", "query": "RRF fusion module"},
    )
    assert response.status_code == 200
    body = response.json()
    source_ids = [src["id"] for src in body["sources"]]
    assert chunk_id in source_ids


def test_get_context_empty_when_workspace_unknown(client: TestClient) -> None:
    _ingest(client, "Just one episode in default.")
    response = client.post(
        "/memory/get_context",
        json={"workspace_id": "other", "query": "anything"},
    )
    assert response.status_code == 200
    assert response.json()["sources"] == []
