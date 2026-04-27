"""End-to-end tests for /memory/get_context."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agent_memory_lite.utils.tokens import estimate_tokens


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


def test_get_context_limits_decisions_by_query(client: TestClient) -> None:
    for index in range(12):
        response = client.post(
            "/memory/write_decision",
            json={
                "workspace_id": "default",
                "title": f"General operating decision {index}",
                "decision_text": "Routine operational preference.",
                "importance": 0.9,
            },
        )
        assert response.status_code == 200, response.text
    target = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "Research agenda stays visible",
            "decision_text": "Research agenda and active theories must not be buried by old decisions.",
            "importance": 0.5,
        },
    )
    assert target.status_code == 200, target.text

    response = client.post(
        "/memory/get_context",
        json={"workspace_id": "default", "query": "research agenda active theories"},
    )

    assert response.status_code == 200, response.text
    text = response.json()["context_text"]
    assert "Research agenda stays visible" in text
    assert text.count("<decision ") <= 4


def test_get_context_applies_budget_to_structured_sections(client: TestClient) -> None:
    long_text = " ".join(["structured context budget should stay bounded"] * 80)
    for index in range(8):
        response = client.post(
            "/memory/write_decision",
            json={
                "workspace_id": "default",
                "title": f"Budget pressure decision {index}",
                "decision_text": long_text,
                "importance": 0.9,
            },
        )
        assert response.status_code == 200, response.text

    role = client.post(
        "/memory/upsert_agent_role",
        json={
            "workspace_id": "default",
            "name": "Budget pressure role",
            "purpose": long_text,
            "responsibilities": [long_text, long_text],
            "confidence": 0.9,
        },
    )
    assert role.status_code == 200, role.text

    context = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "default",
            "query": "budget pressure structured context",
            "max_tokens": 600,
        },
    )
    assert context.status_code == 200, context.text
    text = context.json()["context_text"]
    assert estimate_tokens(text) <= 600
    assert text.count("<decision ") <= 2
