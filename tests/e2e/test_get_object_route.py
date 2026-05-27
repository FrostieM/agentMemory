"""End-to-end tests for v3 discover-then-fetch."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def _write_decision(client: TestClient, **payload):
    return client.post(
        "/memory/write",
        json={"workspace_id": "default", "kind": "decision", "payload": payload},
    )


def test_memory_get_fetches_decision_by_id(client: TestClient) -> None:
    write = _write_decision(
        client,
        title="Discover-then-fetch primer",
        decision_text="Render compact projections first, then fetch full fields on demand.",
        rationale="Stops broad reads from silently wasting token budget.",
    )
    assert write.status_code == 200
    decision_id = write.json()["data"]["decision_id"]

    fetched = client.get(
        "/memory/get",
        params={
            "workspace_id": "default",
            "kind": "decision",
            "id": decision_id,
            "fields": "decision_text,rationale",
        },
    )
    assert fetched.status_code == 200
    obj = fetched.json()["data"]
    assert obj["id"] == decision_id
    assert obj["title"] == "Discover-then-fetch primer"
    assert "compact projections" in obj["decision_text"]


def test_memory_get_returns_not_found_for_unknown_id(client: TestClient) -> None:
    response = client.get(
        "/memory/get",
        params={"workspace_id": "default", "kind": "decision", "id": "dec_does_not_exist"},
    )
    assert response.status_code == 200
    assert response.json()["data"] is None


def test_memory_get_rejects_unknown_kind(client: TestClient) -> None:
    response = client.get(
        "/memory/get",
        params={"workspace_id": "default", "kind": "not_a_kind", "id": "x"},
    )
    assert response.status_code == 400


def test_skill_discover_then_fetch(client: TestClient) -> None:
    skill = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "skill",
            "payload": {
                "name": "Test skill",
                "summary": "Skill for fetch test",
                "body_md": "Skill for fetch test with reusable method details.",
                "trigger": "When fetch tests run.",
                "active": True,
                "confidence": 0.8,
            },
        },
    )
    assert skill.status_code == 200, skill.text
    skill_id = skill.json()["data"]["id"]

    found = client.post(
        "/memory/search",
        json={"workspace_id": "default", "query": "fetch test", "kinds": ["skill"], "limit": 5},
    )
    assert found.status_code == 200, found.text
    assert skill_id in [hit["projection"]["id"] for hit in found.json()["data"]]

    fetched = client.get(
        "/memory/get",
        params={"workspace_id": "default", "kind": "skill", "id": skill_id, "fields": "body_md"},
    )
    assert fetched.status_code == 200, fetched.text
    assert "reusable method details" in fetched.json()["data"]["body_md"]


def test_search_returns_compact_projection_before_full_fetch(client: TestClient) -> None:
    seed_ids: list[str] = []
    for i in range(14):
        r = _write_decision(
            client,
            title=f"Round-trip decision {i:02d}",
            decision_text=f"detail body for round-trip decision {i}",
            importance=0.5,
        )
        assert r.status_code == 200, r.text
        seed_ids.append(r.json()["data"]["decision_id"])

    search = client.post(
        "/memory/search",
        json={
            "workspace_id": "default",
            "query": "round-trip decision",
            "kinds": ["decision"],
            "limit": 5,
        },
    )
    assert search.status_code == 200, search.text
    hits = search.json()["data"]
    assert len(hits) <= 5
    target = hits[0]["projection"]["id"]
    assert target in seed_ids

    fetch = client.get(
        "/memory/get",
        params={"workspace_id": "default", "kind": "decision", "id": target},
    )
    assert fetch.status_code == 200
    assert fetch.json()["data"]["title"].startswith("Round-trip decision")
