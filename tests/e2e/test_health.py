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
    # Shallow default: integrity audit is not run; stub status is "not_checked".
    assert body["retrieval_integrity"]["status"] in {"ok", "unknown", "not_checked"}
    assert isinstance(body["retrieval_integrity"]["counts"], dict)


def test_health_shallow_skips_integrity_audit(client: TestClient) -> None:
    """Default /health must not run the integrity probe — it should be sub-second."""
    response = client.get("/health")
    body = response.json()
    assert body["retrieval_integrity"]["status"] == "not_checked"
    assert body["retrieval_integrity"]["repair_hints"] == [
        "pass ?deep=true to /health to run the integrity audit"
    ]
    assert body["feedback_signal"]["enabled"] is False
    assert body["pending_review"]["total"] == 0


def test_health_deep_runs_full_audit(client: TestClient) -> None:
    """?deep=true triggers the 12-check integrity audit and returns real values."""
    response = client.get("/health?deep=true")
    body = response.json()
    assert response.status_code == 200
    assert body["retrieval_integrity"]["status"] != "not_checked"
    # The integrity counts dict is populated by run_integrity_audit.
    assert body["retrieval_integrity"]["counts"]


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
        # Drift detection only fires in deep mode now — shallow /health is liveness.
        response = client.get("/health?deep=true")
        body = response.json()

    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert body["retrieval_integrity"]["status"] == "degraded"
    assert "fts" in body["retrieval_integrity"]["failures"]
