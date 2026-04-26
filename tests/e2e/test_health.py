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


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["workspace_id"] == "default"
    assert body["embedding_backend"] == "sentence_transformers"
    assert body["embedding_model"] == "intfloat/multilingual-e5-small"
    assert body["vector_backend"] == "lancedb"
    assert body["llm_backend"] == "ollama"
    assert "0001_init" in body["applied_migrations"]
    assert "0002_chunks_fts" in body["applied_migrations"]


def test_health_reports_version(client: TestClient) -> None:
    response = client.get("/health")
    body = response.json()
    assert body["version"]
    assert isinstance(body["version"], str)
