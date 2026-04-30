from __future__ import annotations

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
        assert ok.json()["mode"] == "fts"


def test_required_api_token_fails_fast_when_token_file_missing(
    app_factory,
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="token file is missing"):
        app_factory(
            MEMORY_REQUIRE_API_TOKEN="true",
            MEMORY_API_TOKEN_FILE=str(tmp_path / "missing_token"),
        )
