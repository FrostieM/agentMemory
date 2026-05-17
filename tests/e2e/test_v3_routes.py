"""End-to-end tests for the v3 HTTP surface mounted at /v3/memory/*.

Builds a minimal FastAPI app with just the v3 router — bypasses the
v2 migration bootstrap (which would conflict with v3 schema in the
same DB). Production wiring runs v2 migrations against v2 DBs and
v3 schema against v3 DBs separately.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_memory_lite.v3.api import routes as v3_routes

SCHEMA_V3_PATH = Path(__file__).resolve().parents[2] / "migrations" / "v3" / "0001_init.sql"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """FastAPI client with a pure v3-schema DB and only v3 routes mounted."""
    db_path = tmp_path / "v3.db"

    # Apply v3 schema to a fresh DB.
    init_conn = sqlite3.connect(db_path)
    try:
        init_conn.executescript(SCHEMA_V3_PATH.read_text(encoding="utf-8"))
        init_conn.commit()
    finally:
        init_conn.close()

    # Generator-style dependency: yields a fresh per-request connection.
    def _get_db_gen() -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    from agent_memory_lite.api import deps as real_deps  # noqa: PLC0415 — fixture-local

    app = FastAPI()

    # FastAPI handles generator-style dep correctly (closes on response done).
    app.dependency_overrides[real_deps.get_db_dep] = _get_db_gen
    app.include_router(v3_routes.router)
    with TestClient(app) as c:
        yield c


def _seed_decision(client: TestClient, **kwargs) -> str:
    payload = {"title": "T", "decision_text": "Body of the decision."}
    payload.update(kwargs)
    r = client.post(
        "/v3/memory/write",
        json={"workspace_id": "default", "kind": "decision", "payload": payload},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    return body["data"]["id"]


# ============================================================
# Smoke + envelope shape
# ============================================================


def test_v3_write_decision_returns_envelope(client: TestClient) -> None:
    r = client.post(
        "/v3/memory/write",
        json={
            "workspace_id": "default",
            "kind": "decision",
            "payload": {"title": "A", "decision_text": "Body"},
            "agent_id": "claude",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["kind"] == "decision"
    assert body["data"]["id"].startswith("dec_")


def test_v3_get_returns_projection(client: TestClient) -> None:
    dec_id = _seed_decision(client, title="Hello v3")
    r = client.get(
        "/v3/memory/get",
        params={"workspace_id": "default", "kind": "decision", "id": dec_id},
    )
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["id"] == dec_id
    assert body["data"]["title"] == "Hello v3"
    assert "decision_text" not in body["data"]  # full body NOT in projection


def test_v3_get_with_fields_includes_full(client: TestClient) -> None:
    dec_id = _seed_decision(client, decision_text="The full body text here.")
    r = client.get(
        "/v3/memory/get",
        params={
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "fields": "decision_text,rationale",
        },
    )
    body = r.json()
    assert body["data"]["decision_text"] == "The full body text here."


def test_v3_get_unknown_returns_not_found(client: TestClient) -> None:
    r = client.get(
        "/v3/memory/get",
        params={"workspace_id": "default", "kind": "decision", "id": "missing"},
    )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"


def test_v3_list_returns_projections(client: TestClient) -> None:
    _seed_decision(client, title="A")
    _seed_decision(client, title="B")
    r = client.get(
        "/v3/memory/list",
        params={"workspace_id": "default", "kind": "decision", "limit": 10},
    )
    body = r.json()
    assert body["ok"] is True
    assert len(body["data"]) == 2
    for d in body["data"]:
        assert "decision_text" not in d


def test_v3_count_endpoint(client: TestClient) -> None:
    _seed_decision(client, title="X")
    r = client.get(
        "/v3/memory/count",
        params={"workspace_id": "default", "kind": "decision"},
    )
    assert r.json()["data"]["count"] == 1


def test_v3_search_returns_hits(client: TestClient) -> None:
    _seed_decision(client, title="Kelly sizing", decision_text="Use quarter-Kelly")
    _seed_decision(client, title="Unrelated", decision_text="Other")
    r = client.post(
        "/v3/memory/search",
        json={"workspace_id": "default", "query": "kelly", "limit": 5},
    )
    body = r.json()
    assert body["ok"] is True
    titles = [h["projection"]["title"] for h in body["data"]]
    assert "Kelly sizing" in titles


def test_v3_edit_partial_update(client: TestClient) -> None:
    dec_id = _seed_decision(client, title="v1")
    r = client.post(
        "/v3/memory/edit",
        json={
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "fields": {"status": "superseded"},
        },
    )
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["status"] == "superseded"


def test_v3_pin_decision(client: TestClient) -> None:
    dec_id = _seed_decision(client, title="P")
    r = client.post(
        "/v3/memory/pin",
        json={"workspace_id": "default", "kind": "decision", "id": dec_id, "pinned": True},
    )
    body = r.json()
    assert body["data"]["pinned"] is True


def test_v3_archive_decision(client: TestClient) -> None:
    dec_id = _seed_decision(client, title="A")
    r = client.post(
        "/v3/memory/archive",
        json={
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "reason": "obsolete",
        },
    )
    body = r.json()
    assert body["data"]["status"] == "archived"


def test_v3_brief_composes_session_start(client: TestClient) -> None:
    _seed_decision(client, title="One")
    r = client.get(
        "/v3/memory/brief",
        params={"workspace_id": "default", "max_tokens": 500},
    )
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["token_count"] <= 500
    assert "identity" in body["data"]["sections"]
    assert "default" in body["data"]["body_md"]


def test_v3_lint_empty_workspace_allows(client: TestClient) -> None:
    r = client.post(
        "/v3/memory/lint",
        json={
            "workspace_id": "default",
            "tool_name": "Edit",
            "tool_payload": {"file_path": "x.py"},
        },
    )
    body = r.json()
    assert body["data"]["verdict"] == "allow"


def test_v3_versions_history(client: TestClient) -> None:
    dec_id = _seed_decision(client, title="v1", decision_text="first")
    _seed_decision(client, id=dec_id, title="v2", decision_text="second") if False else None
    # Update via edit to generate a version snapshot
    client.post(
        "/v3/memory/edit",
        json={
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "fields": {"title": "v2"},
        },
    )
    r = client.get(
        "/v3/memory/versions",
        params={"workspace_id": "default", "kind": "decision", "id": dec_id},
    )
    body = r.json()
    assert body["ok"] is True
    assert len(body["data"]) >= 1


def test_v3_rollback_requires_why(client: TestClient) -> None:
    dec_id = _seed_decision(client, title="v1")
    r = client.post(
        "/v3/memory/rollback",
        json={
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "to_version": 1,
            "why": "",
        },
    )
    # Empty why fails Pydantic validation (min_length=1).
    assert r.status_code == 422


def test_v3_write_unsupported_kind_error(client: TestClient) -> None:
    r = client.post(
        "/v3/memory/write",
        json={
            "workspace_id": "default",
            "kind": "nonexistent",
            "payload": {"title": "x"},
        },
    )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unsupported_kind"
