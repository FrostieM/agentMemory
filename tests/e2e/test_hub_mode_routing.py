"""Hub-mode HTTP routing tests.

Without the routing middleware, a request like
``GET /memory/hygiene_report?workspace_id=copyBot`` against a hub-mode
service would silently land on the service's anchor DB and answer for
the wrong workspace. These tests pin the contract: any direct HTTP
caller (``curl``, the local UI, the CLI) gets the same per-workspace
routing the MCP stdio server has had for a while.
"""

from __future__ import annotations

import sqlite3
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
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS


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


def test_explicit_db_header_must_match_write_workspace_id(hub_setup) -> None:
    """An allow-listed explicit DB header still cannot misroute a write body."""
    client, db_a, db_b = hub_setup
    title = "Misrouted explicit header must be rejected"

    response = client.post(
        "/memory/write",
        headers={"X-Memory-DB-Path": str(db_a)},
        json={
            "workspace_id": "ws_b",
            "kind": "decision",
            "payload": {
                "title": title,
                "decision_text": "This must not land in ws_a's physical DB.",
            },
        },
    )

    assert response.status_code == 400, response.text
    assert "wrong database" in response.json()["detail"]
    for path in (db_a, db_b):
        conn = sqlite3.connect(path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE title = ?",
                (title,),
            ).fetchone()[0]
            assert count == 0
        finally:
            conn.close()


def test_explicit_db_header_must_match_research_write_workspace_id(hub_setup) -> None:
    """Non-canonical HTTP write routes must enforce the same physical DB guard."""
    client, db_a, db_b = hub_setup
    title = "Misrouted experiment probe"

    response = client.post(
        "/memory/write_experiment",
        headers={"X-Memory-DB-Path": str(db_a)},
        json={
            "workspace_id": "ws_b",
            "title": title,
            "hypothesis": "This must not land in ws_a's physical DB.",
        },
    )

    assert response.status_code == 400, response.text
    assert "wrong database" in response.json()["detail"]
    for path in (db_a, db_b):
        conn = sqlite3.connect(path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM experiments WHERE title = ?",
                (title,),
            ).fetchone()[0]
            assert count == 0
        finally:
            conn.close()


def test_explicit_db_header_rejects_unregistered_write_workspace_id(hub_setup) -> None:
    """Unknown workspace IDs must not write rows into an explicitly routed DB."""
    client, db_a, db_b = hub_setup
    title = "Unregistered explicit DB header write must be rejected"

    response = client.post(
        "/memory/write",
        headers={"X-Memory-DB-Path": str(db_a)},
        json={
            "workspace_id": "rogue_unregistered",
            "kind": "decision",
            "payload": {
                "title": title,
                "decision_text": "This must not land in any registered DB.",
            },
        },
    )

    assert response.status_code == 400, response.text
    assert "is not registered" in response.json()["detail"]
    for path in (db_a, db_b):
        conn = sqlite3.connect(path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE workspace_id = ? OR title = ?",
                ("rogue_unregistered", title),
            ).fetchone()[0]
            assert count == 0
        finally:
            conn.close()


def test_query_route_rejects_unregistered_body_workspace_id(hub_setup) -> None:
    """A query-routed request cannot smuggle an unregistered body workspace."""
    client, db_a, db_b = hub_setup
    title = "Unregistered body workspace must be rejected"

    response = client.post(
        "/memory/write?workspace_id=ws_a",
        json={
            "workspace_id": "rogue_unregistered",
            "kind": "decision",
            "payload": {
                "title": title,
                "decision_text": "Query routing must not override body ownership.",
            },
        },
    )

    assert response.status_code == 400, response.text
    assert "is not registered" in response.json()["detail"]
    for path in (db_a, db_b):
        conn = sqlite3.connect(path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE workspace_id = ? OR title = ?",
                ("rogue_unregistered", title),
            ).fetchone()[0]
            assert count == 0
        finally:
            conn.close()


def test_episode_db_header_mismatch_rejects_before_vector_deps(
    tmp_path: Path,
    settings_factory,
) -> None:
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
        MEMORY_HUB_MODE="true",
        MEMORY_WORKSPACES_FILE=str(workspaces_file),
    )

    def fail_dep():
        raise AssertionError("DB mismatch must reject before vector dependencies resolve")

    api_deps.reset_dependency_singletons()
    app = create_app(settings)
    app.dependency_overrides[api_deps.get_embedding_provider_dep] = fail_dep
    app.dependency_overrides[api_deps.get_vector_store_dep] = fail_dep
    try:
        with TestClient(app) as client:
            response = client.post(
                "/memory/write",
                headers={"X-Memory-DB-Path": str(db_a)},
                json={
                    "workspace_id": "ws_b",
                    "kind": "episode",
                    "payload": {
                        "source_type": "agent_action",
                        "raw_text": "episode mismatch must not resolve deps",
                    },
                },
            )
        assert response.status_code == 400, response.text
        assert "wrong database" in response.json()["detail"]
    finally:
        api_deps.reset_dependency_singletons()


def test_explicit_db_header_must_match_all_mutation_workspace_ids(hub_setup) -> None:
    client, db_a, db_b = hub_setup

    seed = client.post(
        "/memory/write",
        json={
            "workspace_id": "ws_b",
            "kind": "decision",
            "payload": {
                "title": "Mutation guard seed",
                "decision_text": "Seed decision in ws_b.",
            },
        },
    )
    assert seed.status_code == 200, seed.text
    dec_id = seed.json()["data"]["id"]

    cases = [
        (
            "/memory/edit",
            {
                "workspace_id": "ws_b",
                "kind": "decision",
                "id": dec_id,
                "fields": {"status": "superseded"},
            },
        ),
        (
            "/memory/pin",
            {
                "workspace_id": "ws_b",
                "kind": "decision",
                "id": dec_id,
                "pinned": True,
            },
        ),
        (
            "/memory/archive",
            {
                "workspace_id": "ws_b",
                "kind": "decision",
                "id": dec_id,
                "reason": "misroute probe",
            },
        ),
    ]
    for path, body in cases:
        response = client.post(path, headers={"X-Memory-DB-Path": str(db_a)}, json=body)
        assert response.status_code == 400, response.text
        assert "wrong database" in response.json()["detail"]

    conn_b = sqlite3.connect(db_b)
    conn_b.row_factory = sqlite3.Row
    try:
        row = conn_b.execute(
            "SELECT status, pinned FROM decisions WHERE id = ?",
            (dec_id,),
        ).fetchone()
        assert row is not None
        assert row["status"] == "active"
        assert row["pinned"] == 0
    finally:
        conn_b.close()


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


def test_explicit_db_header_derives_registered_vector_path_for_episode_write(
    tmp_path: Path,
    settings_factory,
    fake_embedding_provider,
    fake_vector_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB override without a vector override still uses that workspace's vector DB."""
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
        MEMORY_HUB_MODE="true",
        MEMORY_WORKSPACES_FILE=str(workspaces_file),
    )
    alpha_store = fake_vector_store
    beta_store = type(fake_vector_store)()
    stores = {vec_a.resolve(): alpha_store, vec_b.resolve(): beta_store}
    built_paths: list[Path] = []

    def fake_build_request_scoped_store(_settings, override: str):
        path = Path(override).resolve()
        built_paths.append(path)
        return stores[path]

    monkeypatch.setattr(api_deps, "_build_request_scoped_store", fake_build_request_scoped_store)
    monkeypatch.setattr(api_deps, "get_vector_store", lambda _settings: alpha_store)

    api_deps.reset_dependency_singletons()
    app = create_app(settings)
    app.dependency_overrides[api_deps.get_embedding_provider_dep] = lambda: fake_embedding_provider
    try:
        with TestClient(app) as client:
            response = client.post(
                "/memory/write",
                headers={"X-Memory-DB-Path": str(db_b)},
                json={
                    "workspace_id": "ws_b",
                    "kind": "episode",
                    "payload": {
                        "source_type": "agent_action",
                        "raw_text": "explicit DB header must route vector path too",
                    },
                },
            )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True, payload
        assert payload["data"]["embedded"] is True
        assert built_paths == [vec_b.resolve()]
        assert beta_store.count(NAMESPACE_CHUNKS, workspace_id="ws_b") == 1
        assert alpha_store.count(NAMESPACE_CHUNKS, workspace_id="ws_b") == 0

        conn_b = sqlite3.connect(db_b)
        conn_b.row_factory = sqlite3.Row
        try:
            chunk = conn_b.execute(
                "SELECT embedding_id FROM chunks WHERE id = ?",
                (payload["data"]["chunk_id"],),
            ).fetchone()
            assert chunk is not None
            assert chunk["embedding_id"] == payload["data"]["chunk_id"]
        finally:
            conn_b.close()
    finally:
        api_deps.reset_dependency_singletons()


def test_memory_write_closes_request_scoped_vector_store(
    tmp_path: Path,
    settings_factory,
    fake_embedding_provider,
    fake_vector_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lazy canonical episode writes must close routed vector stores they open."""
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
        MEMORY_HUB_MODE="true",
        MEMORY_WORKSPACES_FILE=str(workspaces_file),
    )
    closed: list[object] = []

    class ClosingVectorStore(type(fake_vector_store)):
        def close(self) -> None:
            closed.append(self)
            super().close()

    beta_store = ClosingVectorStore()
    built_paths: list[Path] = []

    def fake_build_request_scoped_store(_settings, override: str):
        built_paths.append(Path(override).resolve())
        return beta_store

    monkeypatch.setattr(api_deps, "_build_request_scoped_store", fake_build_request_scoped_store)

    api_deps.reset_dependency_singletons()
    app = create_app(settings)
    app.dependency_overrides[api_deps.get_embedding_provider_dep] = lambda: fake_embedding_provider
    try:
        with TestClient(app) as client:
            response = client.post(
                "/memory/write",
                headers={"X-Memory-DB-Path": str(db_b)},
                json={
                    "workspace_id": "ws_b",
                    "kind": "episode",
                    "payload": {
                        "source_type": "agent_action",
                        "raw_text": "routed vector store must close after write",
                    },
                },
            )
        assert response.status_code == 200, response.text
        assert built_paths == [vec_b.resolve()]
        assert beta_store.count(NAMESPACE_CHUNKS, workspace_id="ws_b") == 1
        assert closed == [beta_store]
    finally:
        api_deps.reset_dependency_singletons()


def test_memory_write_closes_request_scoped_vector_store_when_provider_fails(
    tmp_path: Path,
    settings_factory,
    fake_vector_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If provider setup fails after routed store open, the store is still closed."""
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
        MEMORY_HUB_MODE="true",
        MEMORY_WORKSPACES_FILE=str(workspaces_file),
    )
    closed: list[object] = []

    class ClosingVectorStore(type(fake_vector_store)):
        def close(self) -> None:
            closed.append(self)
            super().close()

    beta_store = ClosingVectorStore()

    def fake_build_request_scoped_store(_settings, override: str):
        assert Path(override).resolve() == vec_b.resolve()
        return beta_store

    def fail_provider():
        raise RuntimeError("provider setup failed after vector store opened")

    monkeypatch.setattr(api_deps, "_build_request_scoped_store", fake_build_request_scoped_store)

    api_deps.reset_dependency_singletons()
    app = create_app(settings)
    app.dependency_overrides[api_deps.get_embedding_provider_dep] = fail_provider
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/memory/write",
                headers={"X-Memory-DB-Path": str(db_b)},
                json={
                    "workspace_id": "ws_b",
                    "kind": "episode",
                    "payload": {
                        "source_type": "agent_action",
                        "raw_text": "provider failure should not leak vector store",
                    },
                },
            )
        assert response.status_code == 500
        assert beta_store.count(NAMESPACE_CHUNKS, workspace_id="ws_b") == 0
        assert closed == [beta_store]
    finally:
        api_deps.reset_dependency_singletons()


def test_explicit_db_header_blank_registered_vector_falls_back_to_anchor_vector(
    tmp_path: Path,
    settings_factory,
    fake_embedding_provider,
    fake_vector_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspaces_file = tmp_path / "workspaces.json"
    db_a = tmp_path / "a" / "memory.db"
    db_b = tmp_path / "b" / "memory.db"
    vec_a = tmp_path / "a" / "vectors.lance"

    _seed_workspace_db(db_a, "ws_a")
    _seed_workspace_db(db_b, "ws_b")
    registry = WorkspaceRegistry(workspaces_file)
    registry.register(workspace_id="ws_b", db_path=str(db_b), vector_path="")

    settings = settings_factory(
        MEMORY_DB_PATH=str(db_a),
        VECTOR_DB_PATH=str(vec_a),
        MEMORY_WORKSPACE_ID="ws_a",
        MEMORY_HUB_MODE="true",
        MEMORY_WORKSPACES_FILE=str(workspaces_file),
    )
    built_paths: list[Path] = []

    def fake_build_request_scoped_store(_settings, override: str):
        path = Path(override).resolve()
        built_paths.append(path)
        return fake_vector_store

    monkeypatch.setattr(api_deps, "_build_request_scoped_store", fake_build_request_scoped_store)

    api_deps.reset_dependency_singletons()
    app = create_app(settings)
    app.dependency_overrides[api_deps.get_embedding_provider_dep] = lambda: fake_embedding_provider
    try:
        with TestClient(app) as client:
            response = client.post(
                "/memory/write",
                headers={"X-Memory-DB-Path": str(db_b)},
                json={
                    "workspace_id": "ws_b",
                    "kind": "episode",
                    "payload": {
                        "source_type": "agent_action",
                        "raw_text": "blank registered vector path should fallback safely",
                    },
                },
            )
        assert response.status_code == 200, response.text
        assert built_paths == [vec_a.resolve()]
        assert fake_vector_store.count(NAMESPACE_CHUNKS, workspace_id="ws_b") == 1
    finally:
        api_deps.reset_dependency_singletons()


def test_conflicting_explicit_db_and_vector_headers_are_rejected(
    tmp_path: Path,
    settings_factory,
    fake_embedding_provider,
    fake_vector_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        MEMORY_HUB_MODE="true",
        MEMORY_WORKSPACES_FILE=str(workspaces_file),
    )
    monkeypatch.setattr(api_deps, "get_vector_store", lambda _settings: fake_vector_store)

    api_deps.reset_dependency_singletons()
    app = create_app(settings)
    app.dependency_overrides[api_deps.get_embedding_provider_dep] = lambda: fake_embedding_provider
    try:
        with TestClient(app) as client:
            response = client.post(
                "/memory/write",
                headers={
                    "X-Memory-DB-Path": str(db_b),
                    "X-Memory-Vector-Path": str(vec_a),
                },
                json={
                    "workspace_id": "ws_b",
                    "kind": "episode",
                    "payload": {
                        "source_type": "agent_action",
                        "raw_text": "conflicting registered headers must be rejected",
                    },
                },
            )
        assert response.status_code == 400
        assert "does not match" in response.json()["detail"]
        assert fake_vector_store.count(NAMESPACE_CHUNKS, workspace_id="ws_b") == 0
    finally:
        api_deps.reset_dependency_singletons()


def test_vector_header_without_db_header_must_match_anchor_vector(
    tmp_path: Path,
    settings_factory,
    fake_embedding_provider,
    fake_vector_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    beta_store = type(fake_vector_store)()
    built_paths: list[Path] = []

    def fake_build_request_scoped_store(_settings, override: str):
        path = Path(override).resolve()
        built_paths.append(path)
        return beta_store

    monkeypatch.setattr(api_deps, "_build_request_scoped_store", fake_build_request_scoped_store)

    api_deps.reset_dependency_singletons()
    app = create_app(settings)
    app.dependency_overrides[api_deps.get_embedding_provider_dep] = lambda: fake_embedding_provider
    try:
        with TestClient(app) as client:
            response = client.post(
                "/memory/write",
                headers={"X-Memory-Vector-Path": str(vec_b)},
                json={
                    "workspace_id": "ws_a",
                    "kind": "episode",
                    "payload": {
                        "source_type": "agent_action",
                        "raw_text": "vector-only override must match active DB",
                    },
                },
            )
        assert response.status_code == 400
        assert "does not match the active database" in response.json()["detail"]
        assert built_paths == []
        assert beta_store.count(NAMESPACE_CHUNKS, workspace_id="ws_a") == 0
    finally:
        api_deps.reset_dependency_singletons()


def test_invalid_vector_header_rejects_before_embedding_provider(
    tmp_path: Path,
    settings_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    def fail_provider():
        raise AssertionError("invalid vector header must reject before provider resolves")

    def fake_build_request_scoped_store(_settings, override: str):
        raise AssertionError(f"invalid vector header should not build store: {override}")

    monkeypatch.setattr(api_deps, "_build_request_scoped_store", fake_build_request_scoped_store)

    api_deps.reset_dependency_singletons()
    app = create_app(settings)
    app.dependency_overrides[api_deps.get_embedding_provider_dep] = fail_provider
    try:
        with TestClient(app) as client:
            response = client.post(
                "/memory/write",
                headers={"X-Memory-Vector-Path": str(vec_b)},
                json={
                    "workspace_id": "ws_a",
                    "kind": "episode",
                    "payload": {
                        "source_type": "agent_action",
                        "raw_text": "bad vector header should beat provider setup",
                    },
                },
            )
        assert response.status_code == 400
        assert "does not match the active database" in response.json()["detail"]
    finally:
        api_deps.reset_dependency_singletons()


def test_anchor_db_header_rejects_foreign_registered_vector_header(
    tmp_path: Path,
    settings_factory,
    fake_embedding_provider,
    fake_vector_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The allow-listed anchor DB cannot be paired with another workspace's vector path."""
    workspaces_file = tmp_path / "workspaces.json"
    db_a = tmp_path / "a" / "memory.db"
    db_b = tmp_path / "b" / "memory.db"
    vec_a = tmp_path / "a" / "vectors.lance"
    vec_b = tmp_path / "b" / "vectors.lance"

    _seed_workspace_db(db_a, "ws_a")
    _seed_workspace_db(db_b, "ws_b")
    registry = WorkspaceRegistry(workspaces_file)
    registry.register(workspace_id="ws_b", db_path=str(db_b), vector_path=str(vec_b))

    settings = settings_factory(
        MEMORY_DB_PATH=str(db_a),
        VECTOR_DB_PATH=str(vec_a),
        MEMORY_WORKSPACE_ID="ws_a",
        MEMORY_HUB_MODE="true",
        MEMORY_WORKSPACES_FILE=str(workspaces_file),
    )
    monkeypatch.setattr(api_deps, "get_vector_store", lambda _settings: fake_vector_store)

    api_deps.reset_dependency_singletons()
    app = create_app(settings)
    app.dependency_overrides[api_deps.get_embedding_provider_dep] = lambda: fake_embedding_provider
    try:
        with TestClient(app) as client:
            response = client.post(
                "/memory/write",
                headers={
                    "X-Memory-DB-Path": str(db_a),
                    "X-Memory-Vector-Path": str(vec_b),
                },
                json={
                    "workspace_id": "ws_a",
                    "kind": "episode",
                    "payload": {
                        "source_type": "agent_action",
                        "raw_text": "anchor db cannot pair with foreign vector path",
                    },
                },
            )
        assert response.status_code == 400
        assert "does not match" in response.json()["detail"]
        assert fake_vector_store.count(NAMESPACE_CHUNKS, workspace_id="ws_a") == 0
    finally:
        api_deps.reset_dependency_singletons()
