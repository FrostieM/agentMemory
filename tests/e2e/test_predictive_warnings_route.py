"""End-to-end tests for /memory/predictive_warnings (v3.1 Vector 5)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_predictive_warnings_get_empty_workspace(client: TestClient) -> None:
    """GET returns 200 on a fresh canonical DB with no warnings."""
    r = client.get("/memory/predictive_warnings", params={"workspace_id": "default"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace_id"] == "default"
    assert body["warnings"] == []
    assert body["available"] is True
    assert body["feature_enabled"] is True


def test_predictive_warnings_respects_disabled_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When MEMORY_PREDICTIVE_FAILURE_ENABLED=false the scanner short-
    circuits to an empty list — but the route still returns 200 so
    the operator can introspect the flag state."""
    monkeypatch.setenv("MEMORY_PREDICTIVE_FAILURE_ENABLED", "false")
    r = client.get("/memory/predictive_warnings", params={"workspace_id": "default"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["warnings"] == []
    assert body["feature_enabled"] is False


def test_predictive_warnings_rejects_invalid_limit(client: TestClient) -> None:
    r = client.get(
        "/memory/predictive_warnings",
        params={"workspace_id": "default", "limit": 0},
    )
    assert r.status_code == 422
    r2 = client.get(
        "/memory/predictive_warnings",
        params={"workspace_id": "default", "limit": 100},
    )
    assert r2.status_code == 422


def test_predictive_warnings_requires_workspace_id(client: TestClient) -> None:
    r = client.get("/memory/predictive_warnings")
    assert r.status_code == 422
