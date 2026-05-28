"""Canonical /memory/write episode writes include semantic vector indexing."""

from __future__ import annotations

import sqlite3

from fastapi import Request
from fastapi.testclient import TestClient

from agent_memory_lite.api import deps as api_deps
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS


def test_memory_write_non_episode_does_not_resolve_embedding_deps(app_factory) -> None:
    app = app_factory(MEMORY_WORKSPACE_ID="default")

    def fail_dep():
        raise AssertionError("non-episode writes must not resolve vector dependencies")

    app.dependency_overrides[api_deps.get_embedding_provider_dep] = fail_dep
    app.dependency_overrides[api_deps.get_vector_store_dep] = fail_dep

    with TestClient(app) as client:
        response = client.post(
            "/memory/write",
            json={
                "workspace_id": "default",
                "kind": "decision",
                "payload": {
                    "title": "Non-episode write stays scalar",
                    "decision_text": "Decision writes should not require embedding dependencies.",
                },
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True, payload
    assert payload["data"]["title"] == "Non-episode write stays scalar"


def test_memory_write_episode_embeds_through_canonical_route(
    app_factory,
    fake_embedding_provider,
    fake_vector_store,
    tmp_db_path,
) -> None:
    app = app_factory(MEMORY_WORKSPACE_ID="default")
    with TestClient(app) as client:
        response = client.post(
            "/memory/write",
            json={
                "workspace_id": "default",
                "kind": "episode",
                "payload": {
                    "source_type": "agent_action",
                    "raw_text": "canonical HTTP episode should be embedded",
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True, payload
    assert payload["data"]["embedded"] is True
    chunk_id = payload["data"]["chunk_id"]
    assert fake_vector_store.count(NAMESPACE_CHUNKS, workspace_id="default") == 1

    conn = sqlite3.connect(tmp_db_path)
    conn.row_factory = sqlite3.Row
    try:
        chunk = conn.execute("SELECT embedding_id FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        assert chunk is not None
        assert chunk["embedding_id"] == chunk_id
        metadata = conn.execute(
            """
            SELECT embedding_dim, provider_name, row_count
            FROM vector_index_metadata
            WHERE workspace_id = 'default' AND namespace = ?
            """,
            (NAMESPACE_CHUNKS,),
        ).fetchone()
        assert metadata is not None
        assert metadata["row_count"] == 1
        assert metadata["provider_name"] == fake_embedding_provider.name
        assert metadata["embedding_dim"] == fake_embedding_provider.dim
    finally:
        conn.close()


def test_memory_write_episode_accepts_fastapi_style_dependency_overrides(
    app_factory,
    fake_embedding_provider,
    fake_vector_store,
    tmp_db_path,
) -> None:
    app = app_factory(MEMORY_WORKSPACE_ID="default")
    seen: dict[str, object] = {}

    def provider_override(settings):
        seen["provider_workspace"] = settings.workspace_id
        return fake_embedding_provider

    def store_override(request, settings):
        seen["store_workspace"] = settings.workspace_id
        seen["path"] = request.url.path
        return fake_vector_store

    app.dependency_overrides[api_deps.get_embedding_provider_dep] = provider_override
    app.dependency_overrides[api_deps.get_vector_store_dep] = store_override

    with TestClient(app) as client:
        response = client.post(
            "/memory/write",
            json={
                "workspace_id": "default",
                "kind": "episode",
                "payload": {
                    "source_type": "agent_action",
                    "raw_text": "canonical HTTP episode should support typed overrides",
                },
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True, payload
    assert payload["data"]["embedded"] is True
    assert seen == {
        "provider_workspace": "default",
        "store_workspace": "default",
        "path": "/memory/write",
    }

    conn = sqlite3.connect(tmp_db_path)
    conn.row_factory = sqlite3.Row
    try:
        metadata = conn.execute(
            """
            SELECT embedding_dim, provider_name, row_count
            FROM vector_index_metadata
            WHERE workspace_id = 'default' AND namespace = ?
            """,
            (NAMESPACE_CHUNKS,),
        ).fetchone()
        assert metadata is not None
        assert metadata["provider_name"] == fake_embedding_provider.name
        assert metadata["embedding_dim"] == fake_embedding_provider.dim
        assert metadata["row_count"] == 1
    finally:
        conn.close()


def test_memory_write_episode_accepts_single_request_dependency_override(
    app_factory,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    app = app_factory(MEMORY_WORKSPACE_ID="default")
    seen: dict[str, object] = {}

    def provider_override(settings):
        return fake_embedding_provider

    def store_override(req: Request):
        seen["request_type"] = type(req).__name__
        seen["path"] = req.url.path
        return fake_vector_store

    app.dependency_overrides[api_deps.get_embedding_provider_dep] = provider_override
    app.dependency_overrides[api_deps.get_vector_store_dep] = store_override

    with TestClient(app) as client:
        response = client.post(
            "/memory/write",
            json={
                "workspace_id": "default",
                "kind": "episode",
                "payload": {
                    "source_type": "agent_action",
                    "raw_text": "single request override should receive the request object",
                },
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True, payload
    assert payload["data"]["embedded"] is True
    assert seen == {"request_type": "Request", "path": "/memory/write"}


def test_memory_write_episode_accepts_annotated_request_dependency_override(
    app_factory,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    app = app_factory(MEMORY_WORKSPACE_ID="default")
    seen: dict[str, object] = {}

    def provider_override(settings):
        return fake_embedding_provider

    def store_override(r: Request):
        seen["request_type"] = type(r).__name__
        seen["path"] = r.url.path
        return fake_vector_store

    app.dependency_overrides[api_deps.get_embedding_provider_dep] = provider_override
    app.dependency_overrides[api_deps.get_vector_store_dep] = store_override

    with TestClient(app) as client:
        response = client.post(
            "/memory/write",
            json={
                "workspace_id": "default",
                "kind": "episode",
                "payload": {
                    "source_type": "agent_action",
                    "raw_text": "annotated request override should receive the request object",
                },
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True, payload
    assert payload["data"]["embedded"] is True
    assert seen == {"request_type": "Request", "path": "/memory/write"}


def test_memory_write_episode_accepts_yield_dependency_overrides(
    app_factory,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    app = app_factory(MEMORY_WORKSPACE_ID="default")
    finalized: list[str] = []

    def provider_override():
        try:
            yield fake_embedding_provider
        finally:
            finalized.append("provider")

    def store_override():
        try:
            yield fake_vector_store
        finally:
            finalized.append("store")

    app.dependency_overrides[api_deps.get_embedding_provider_dep] = provider_override
    app.dependency_overrides[api_deps.get_vector_store_dep] = store_override

    with TestClient(app) as client:
        response = client.post(
            "/memory/write",
            json={
                "workspace_id": "default",
                "kind": "episode",
                "payload": {
                    "source_type": "agent_action",
                    "raw_text": "yield override should embed and finalize",
                },
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True, payload
    assert payload["data"]["embedded"] is True
    assert fake_vector_store.count(NAMESPACE_CHUNKS, workspace_id="default") == 1
    assert finalized == ["provider", "store"]


def test_memory_write_invalid_episode_rejects_before_vector_deps(app_factory) -> None:
    app = app_factory(MEMORY_WORKSPACE_ID="default")

    def fail_dep():
        raise AssertionError("invalid episode payload must reject before vector dependencies")

    app.dependency_overrides[api_deps.get_embedding_provider_dep] = fail_dep
    app.dependency_overrides[api_deps.get_vector_store_dep] = fail_dep

    with TestClient(app) as client:
        response = client.post(
            "/memory/write",
            json={
                "workspace_id": "default",
                "kind": "episode",
                "payload": {"raw_text": "missing required source_type"},
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is False, payload
    assert payload["error"]["code"] == "invalid_args"
