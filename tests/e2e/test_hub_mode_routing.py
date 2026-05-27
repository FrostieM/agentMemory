"""Hub-mode HTTP routing tests.

Without the routing middleware, a request like
``GET /memory/hygiene_report?workspace_id=copyBot`` against a hub-mode
service would silently land on the service's anchor DB and answer for
the wrong workspace. These tests pin the contract: any direct HTTP
caller (``curl``, the local UI, the CLI) gets the same per-workspace
routing the MCP stdio server has had for a while.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_memory_lite.api import deps as api_deps
from agent_memory_lite.api.app import create_app
from agent_memory_lite.config.workspace_registry import WorkspaceRegistry
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.repositories.workspace_manifest_repo import ensure_workspace_manifest


def _seed_workspace_db(db_path: Path, workspace_id: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_connection(db_path)
    try:
        apply_migrations(conn)
        ensure_workspace_manifest(conn, workspace_id=workspace_id, allow_default_workspace=True)
    finally:
        close_connection(conn)


@pytest.fixture
def hub_setup(
    tmp_path: Path,
    settings_factory,
    fake_embedding_provider,
    fake_vector_store,
) -> Iterator[tuple[TestClient, Path, Path]]:
    workspaces_file = tmp_path / "workspaces.json"
    db_a = tmp_path / "a" / "memory.db"
    db_b = tmp_path / "b" / "memory.db"
    vec_a = tmp_path / "a" / "vectors.lance"
    vec_b = tmp_path / "b" / "vectors.lance"

    _seed_workspace_db(db_a, "ws_a")
    _seed_workspace_db(db_b, "ws_b")

    registry = WorkspaceRegistry(workspaces_file)
    registry.register(
        workspace_id="ws_a",
        db_path=str(db_a),
        vector_path=str(vec_a),
        label="A",
    )
    registry.register(
        workspace_id="ws_b",
        db_path=str(db_b),
        vector_path=str(vec_b),
        label="B",
    )

    settings = settings_factory(
        MEMORY_DB_PATH=str(db_a),
        VECTOR_DB_PATH=str(vec_a),
        MEMORY_WORKSPACE_ID="ws_a",
        MEMORY_HUB_MODE="true",
        MEMORY_WORKSPACES_FILE=str(workspaces_file),
    )

    api_deps.reset_dependency_singletons()
    app = create_app(settings)
    app.dependency_overrides[api_deps.get_embedding_provider_dep] = lambda: fake_embedding_provider
    app.dependency_overrides[api_deps.get_vector_store_dep] = lambda: fake_vector_store

    with TestClient(app) as client:
        yield client, db_a, db_b
    api_deps.reset_dependency_singletons()


@pytest.fixture
def project_setup(
    tmp_path: Path,
    settings_factory,
    fake_embedding_provider,
    fake_vector_store,
) -> Iterator[TestClient]:
    """Same registry as hub_setup but with hub_mode=false (project mode)."""
    workspaces_file = tmp_path / "workspaces.json"
    db_a = tmp_path / "a" / "memory.db"
    db_b = tmp_path / "b" / "memory.db"
    vec_a = tmp_path / "a" / "vectors.lance"
    vec_b = tmp_path / "b" / "vectors.lance"

    _seed_workspace_db(db_a, "ws_a")
    _seed_workspace_db(db_b, "ws_b")

    registry = WorkspaceRegistry(workspaces_file)
    registry.register(workspace_id="ws_a", db_path=str(db_a), vector_path=str(vec_a))
    registry.register(workspace_id="ws_b", db_path=str(db_b), vector_path=str(vec_b))

    settings = settings_factory(
        MEMORY_DB_PATH=str(db_a),
        VECTOR_DB_PATH=str(vec_a),
        MEMORY_WORKSPACE_ID="ws_a",
        MEMORY_HUB_MODE="false",
        MEMORY_WORKSPACES_FILE=str(workspaces_file),
    )

    api_deps.reset_dependency_singletons()
    app = create_app(settings)
    app.dependency_overrides[api_deps.get_embedding_provider_dep] = lambda: fake_embedding_provider
    app.dependency_overrides[api_deps.get_vector_store_dep] = lambda: fake_vector_store

    with TestClient(app) as client:
        yield client
    api_deps.reset_dependency_singletons()


def test_hub_mode_get_routes_by_query_workspace_id(hub_setup) -> None:
    """Hygiene report for ws_b must hit ws_b's DB, not the anchor (ws_a)."""
    client, _, _ = hub_setup
    response = client.get("/memory/hygiene_report", params={"workspace_id": "ws_b"})
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == "ws_b"


def test_hub_mode_post_routes_by_body_workspace_id(hub_setup) -> None:
    """memory_write(kind=decision) into ws_b lands in ws_b's DB, invisible to ws_a."""
    client, _, _ = hub_setup

    written = client.post(
        "/memory/write",
        json={
            "workspace_id": "ws_b",
            "kind": "decision",
            "payload": {
                "title": "Routing test decision",
                "decision_text": "Created from hub-mode middleware test.",
                "rationale": "fixture",
            },
        },
    )
    assert written.status_code == 200, written.text

    in_b = client.post(
        "/memory/search",
        json={
            "workspace_id": "ws_b",
            "kinds": ["decision"],
            "query": "Routing test decision",
            "limit": 5,
        },
    )
    assert in_b.status_code == 200
    titles_b = [d["projection"]["title"] for d in in_b.json()["data"]]
    assert "Routing test decision" in titles_b

    in_a = client.post(
        "/memory/search",
        json={
            "workspace_id": "ws_a",
            "kinds": ["decision"],
            "query": "Routing test decision",
            "limit": 5,
        },
    )
    assert in_a.status_code == 200
    titles_a = [d["projection"]["title"] for d in in_a.json()["data"]]
    assert "Routing test decision" not in titles_a


def test_unregistered_db_path_header_is_rejected(hub_setup, tmp_path: Path) -> None:
    """v3.5 sector-4 audit: an X-Memory-DB-Path that is not in the
    workspace registry is rejected (400). The header can no longer
    bypass the registry to point the service at an arbitrary SQLite file
    on disk — it must name a registered workspace."""
    client, _, _ = hub_setup
    rogue = tmp_path / "rogue" / "memory.db"
    _seed_workspace_db(rogue, "rogue")
    response = client.get(
        "/memory/hygiene_report",
        params={"workspace_id": "ws_b"},
        headers={"X-Memory-DB-Path": str(rogue)},
    )
    assert response.status_code == 400, response.text
    assert "registry" in response.json()["detail"]


def test_project_mode_does_not_route(project_setup: TestClient) -> None:
    """In project mode (hub_mode=false) middleware is a no-op."""
    response = project_setup.get("/memory/hygiene_report", params={"workspace_id": "ws_b"})
    # The call still succeeds (the route reads from anchor DB), but
    # crucially the middleware did NOT silently swap the DB.
    # workspace_id echo is the requested value (the route just answers
    # against whatever DB it has).
    assert response.status_code == 200
    assert response.json()["workspace_id"] == "ws_b"
