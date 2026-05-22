"""End-to-end tests for the canonical HTTP surface mounted at /memory/*.

Builds a minimal FastAPI app with just the canonical router — bypasses the
v2 migration bootstrap (which would conflict with the canonical schema in the
same DB). Production wiring runs v2 migrations against v2 DBs and
canonical schema against canonical DBs separately.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_memory_lite.api.routes import memory as v3_routes

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "migrations" / "canonical" / "0001_init.sql"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """FastAPI client with a pure canonical-schema DB and only canonical routes mounted."""
    db_path = tmp_path / "canonical.db"

    # Apply canonical schema to a fresh DB.
    init_conn = sqlite3.connect(db_path)
    try:
        init_conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
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
        "/memory/write",
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
        "/memory/write",
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
    dec_id = _seed_decision(client, title="Hello canonical")
    r = client.get(
        "/memory/get",
        params={"workspace_id": "default", "kind": "decision", "id": dec_id},
    )
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["id"] == dec_id
    assert body["data"]["title"] == "Hello canonical"
    assert "decision_text" not in body["data"]  # full body NOT in projection


def test_v3_get_with_fields_includes_full(client: TestClient) -> None:
    dec_id = _seed_decision(client, decision_text="The full body text here.")
    r = client.get(
        "/memory/get",
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
        "/memory/get",
        params={"workspace_id": "default", "kind": "decision", "id": "missing"},
    )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"


def test_v3_list_returns_projections(client: TestClient) -> None:
    _seed_decision(client, title="A")
    _seed_decision(client, title="B")
    r = client.get(
        "/memory/list",
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
        "/memory/count",
        params={"workspace_id": "default", "kind": "decision"},
    )
    assert r.json()["data"]["count"] == 1


def test_v3_search_returns_hits(client: TestClient) -> None:
    _seed_decision(client, title="Kelly sizing", decision_text="Use quarter-Kelly")
    _seed_decision(client, title="Unrelated", decision_text="Other")
    r = client.post(
        "/memory/search",
        json={"workspace_id": "default", "query": "kelly", "limit": 5},
    )
    body = r.json()
    assert body["ok"] is True
    titles = [h["projection"]["title"] for h in body["data"]]
    assert "Kelly sizing" in titles


def test_v3_edit_partial_update(client: TestClient) -> None:
    dec_id = _seed_decision(client, title="v1")
    r = client.post(
        "/memory/edit",
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
        "/memory/pin",
        json={"workspace_id": "default", "kind": "decision", "id": dec_id, "pinned": True},
    )
    body = r.json()
    assert body["data"]["pinned"] is True


def test_v3_archive_decision(client: TestClient) -> None:
    dec_id = _seed_decision(client, title="A")
    r = client.post(
        "/memory/archive",
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
        "/memory/brief",
        params={"workspace_id": "default", "max_tokens": 500},
    )
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["token_count"] <= 500
    assert "identity" in body["data"]["sections"]
    assert "default" in body["data"]["body_md"]


def test_v3_lint_empty_workspace_allows(client: TestClient) -> None:
    r = client.post(
        "/memory/lint",
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
        "/memory/edit",
        json={
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "fields": {"title": "v2"},
        },
    )
    r = client.get(
        "/memory/versions",
        params={"workspace_id": "default", "kind": "decision", "id": dec_id},
    )
    body = r.json()
    assert body["ok"] is True
    assert len(body["data"]) >= 1


def test_v3_rollback_requires_why(client: TestClient) -> None:
    dec_id = _seed_decision(client, title="v1")
    r = client.post(
        "/memory/rollback",
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


def test_v3_impact_check_not_indexed(client: TestClient) -> None:
    """File with no code_digests row → not_indexed verdict."""
    r = client.get(
        "/memory/impact_check",
        params={"workspace_id": "default", "file_path": "src/missing.py"},
    )
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["verdict"] == "not_indexed"
    assert "not in code_digests" in body["data"]["advisory"]


def test_v3_impact_check_envelope_shape(client: TestClient) -> None:
    r = client.get(
        "/memory/impact_check",
        params={"workspace_id": "default", "file_path": "src/x.py"},
    )
    body = r.json()
    assert body["ok"] is True
    assert set(body["data"].keys()) == {
        "file_path",
        "digest",
        "callers",
        "hot_symbols",
        "verdict",
        "advisory",
    }


def test_v3_write_unsupported_kind_error(client: TestClient) -> None:
    r = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "nonexistent",
            "payload": {"title": "x"},
        },
    )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unsupported_kind"


def _seed_plan_step(client: TestClient, task_id: str, title: str) -> None:
    r = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "plan_step",
            "payload": {"task_id": task_id, "title": title},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_v3_plan_returns_steps_rank_ordered(client: TestClient) -> None:
    for title in ("first", "second", "third"):
        _seed_plan_step(client, "t1", title)
    r = client.get("/memory/plan", params={"workspace_id": "default", "task_id": "t1"})
    body = r.json()
    assert body["ok"] is True
    assert [s["title"] for s in body["data"]] == ["first", "second", "third"]
    assert all(s["kind"] == "plan_step" for s in body["data"])


def test_v3_plan_empty_for_unknown_task(client: TestClient) -> None:
    r = client.get("/memory/plan", params={"workspace_id": "default", "task_id": "nope"})
    body = r.json()
    assert body["ok"] is True
    assert body["data"] == []
