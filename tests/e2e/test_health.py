from __future__ import annotations

import sqlite3
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
    assert body["retrieval_integrity"]["status"] in {"ok", "unknown"}
    assert isinstance(body["retrieval_integrity"]["counts"], dict)


def test_health_reports_version(client: TestClient) -> None:
    response = client.get("/health")
    body = response.json()
    assert body["version"]
    assert isinstance(body["version"], str)


def test_health_degrades_on_fts_drift(app_factory, tmp_db_path) -> None:
    app = app_factory()
    with TestClient(app) as client:
        ingest = client.post(
            "/memory/ingest_episode",
            json={
                "workspace_id": "default",
                "source_type": "agent_action",
                "raw_text": "health drift control token",
            },
        )
        assert ingest.status_code == 200
        chunk_id = ingest.json()["chunk_id"]

    conn = sqlite3.connect(tmp_db_path)
    try:
        conn.execute("UPDATE chunks_fts SET workspace_id = 'other' WHERE chunk_id = ?", (chunk_id,))
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.get("/health")
        body = response.json()

    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert body["retrieval_integrity"]["status"] == "degraded"
    assert "fts" in body["retrieval_integrity"]["failures"]
