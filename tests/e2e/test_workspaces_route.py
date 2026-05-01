"""End-to-end tests for the hub workspace registry endpoints."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory, tmp_path: Path) -> Iterator[TestClient]:
    workspaces_file = tmp_path / "workspaces.json"
    app = app_factory(MEMORY_WORKSPACES_FILE=str(workspaces_file), MEMORY_HUB_MODE="true")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def forbid_default_client(app_factory, tmp_path: Path) -> Iterator[TestClient]:
    workspaces_file = tmp_path / "workspaces_forbid.json"
    app = app_factory(
        MEMORY_WORKSPACES_FILE=str(workspaces_file),
        MEMORY_HUB_MODE="true",
        MEMORY_FORBID_DEFAULT_WORKSPACE="true",
        MEMORY_WORKSPACE_ID="anchor",
    )
    with TestClient(app) as c:
        yield c


def test_list_workspaces_includes_anchor_when_registry_empty(client: TestClient) -> None:
    response = client.get("/memory/workspaces")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hub_mode"] is True
    assert body["current_workspace_id"] == "default"
    workspaces = body["workspaces"]
    assert len(workspaces) == 1
    assert workspaces[0]["id"] == "default"
    assert workspaces[0]["is_current"] is True


def test_register_then_list_returns_entry(client: TestClient, tmp_path: Path) -> None:
    register = client.post(
        "/memory/workspaces",
        json={
            "workspace_id": "alpha",
            "db_path": str(tmp_path / "alpha" / "memory.db"),
            "vector_path": str(tmp_path / "alpha" / "vectors.lance"),
            "label": "Alpha project",
            "project_root": str(tmp_path / "alpha"),
        },
    )
    assert register.status_code == 200, register.text
    workspaces = client.get("/memory/workspaces").json()["workspaces"]
    ids = {w["id"] for w in workspaces}
    assert "alpha" in ids
    alpha = next(w for w in workspaces if w["id"] == "alpha")
    assert alpha["label"] == "Alpha project"
    assert alpha["db_path"].endswith("memory.db")


def test_register_default_workspace_rejected_when_forbidden(
    forbid_default_client: TestClient,
) -> None:
    response = forbid_default_client.post(
        "/memory/workspaces",
        json={
            "workspace_id": "default",
            "db_path": "/tmp/default.db",
            "vector_path": "/tmp/default.lance",
        },
    )
    assert response.status_code in (400, 422)
    detail = json.dumps(response.json()).lower()
    assert "forbid" in detail or "default" in detail


def test_delete_workspace_removes_entry(client: TestClient, tmp_path: Path) -> None:
    client.post(
        "/memory/workspaces",
        json={
            "workspace_id": "beta",
            "db_path": str(tmp_path / "beta" / "memory.db"),
            "vector_path": str(tmp_path / "beta" / "vectors.lance"),
        },
    ).raise_for_status()
    response = client.delete("/memory/workspaces/beta")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["removed"] is True
    workspaces = client.get("/memory/workspaces").json()["workspaces"]
    assert "beta" not in {w["id"] for w in workspaces}


def test_register_is_idempotent_and_updates_label(client: TestClient, tmp_path: Path) -> None:
    db = str(tmp_path / "gamma" / "memory.db")
    vec = str(tmp_path / "gamma" / "vectors.lance")
    first = client.post(
        "/memory/workspaces",
        json={"workspace_id": "gamma", "db_path": db, "vector_path": vec, "label": "old"},
    ).json()
    second = client.post(
        "/memory/workspaces",
        json={"workspace_id": "gamma", "db_path": db, "vector_path": vec, "label": "new"},
    ).json()
    assert first["workspace"]["registered_at"] == second["workspace"]["registered_at"]
    assert second["workspace"]["label"] == "new"
    workspaces = client.get("/memory/workspaces").json()["workspaces"]
    gamma_entries = [w for w in workspaces if w["id"] == "gamma"]
    assert len(gamma_entries) == 1
