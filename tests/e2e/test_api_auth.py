from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_memory_routes_require_bearer_token_when_enabled(app_factory, tmp_path: Path) -> None:
    token_file = tmp_path / "memory_token"
    token_file.write_text("local-secret\n", encoding="utf-8")
    app = app_factory(
        MEMORY_REQUIRE_API_TOKEN="true",
        MEMORY_API_TOKEN_FILE=str(token_file),
    )

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200

        missing = client.post("/memory/search", json={"workspace_id": "default", "query": "x"})
        assert missing.status_code == 401

        wrong = client.post(
            "/memory/search",
            json={"workspace_id": "default", "query": "x"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert wrong.status_code == 401

        ok = client.post(
            "/memory/search",
            json={"workspace_id": "default", "query": "x"},
            headers={"Authorization": "Bearer local-secret"},
        )
        assert ok.status_code == 200
        body = ok.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)


def test_required_api_token_fails_fast_when_token_file_missing(
    app_factory,
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="token file is missing"):
        app_factory(
            MEMORY_REQUIRE_API_TOKEN="true",
            MEMORY_API_TOKEN_FILE=str(tmp_path / "missing_token"),
        )


def test_api_auth_failure_can_be_audited(
    app_factory,
    tmp_path: Path,
    tmp_db_path: Path,
) -> None:
    token_file = tmp_path / "memory_token"
    token_file.write_text("local-secret\n", encoding="utf-8")
    app = app_factory(
        MEMORY_REQUIRE_API_TOKEN="true",
        MEMORY_AUDIT_API_AUTH_FAILURES="true",
        MEMORY_API_TOKEN_FILE=str(token_file),
    )

    with TestClient(app) as client:
        response = client.post(
            "/memory/search",
            json={"workspace_id": "default", "query": "x"},
            headers={"Authorization": "Bearer wrong"},
        )

    assert response.status_code == 401

    conn = sqlite3.connect(tmp_db_path)
    try:
        row = conn.execute(
            """
            SELECT kind, workspace_id, summary, details_json
            FROM maintenance_events
            WHERE kind = 'api_auth_failure'
            """
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "api_auth_failure"
    assert row[1] == "default"
    assert "invalid_bearer_token" in row[2]
    assert "wrong" not in row[3]
