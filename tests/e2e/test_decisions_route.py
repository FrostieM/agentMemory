from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_write_decision_returns_active(client: TestClient) -> None:
    response = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "Use SQLite + LanceDB",
            "decision_text": "Lite memory uses SQLite + LanceDB.",
            "rationale": "no Docker available",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert body["decision_id"].startswith("dec_")


def test_supersedes_chain_via_http(client: TestClient) -> None:
    first = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "v1",
            "decision_text": "first",
        },
    ).json()
    second = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "v2",
            "decision_text": "second",
            "supersedes_decision_id": first["decision_id"],
        },
    ).json()
    assert second["superseded_decision_id"] == first["decision_id"]


def test_unknown_supersedes_returns_404(client: TestClient) -> None:
    response = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "x",
            "decision_text": "y",
            "supersedes_decision_id": "dec_missing",
        },
    )
    assert response.status_code == 404


def test_list_decisions_searches_by_topic(client: TestClient) -> None:
    first = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "Live execution watchdog",
            "decision_text": "Keep live execution HTTP health cache-backed and non-blocking.",
            "rationale": "Operators need the topic-level decision without knowing its id.",
            "importance": 0.9,
        },
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "Frontend layout",
            "decision_text": "Use operator-first dashboards.",
            "importance": 0.9,
        },
    )
    assert second.status_code == 200, second.text

    response = client.post(
        "/memory/list_decisions",
        json={"workspace_id": "default", "query": "live execution health", "limit": 1},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["title"] for item in body["decisions"]] == ["Live execution watchdog"]
    assert body["decisions"][0]["decision_text"].endswith("non-blocking.")


def test_list_decisions_repairs_display_mojibake(client: TestClient) -> None:
    mojibake_dash = "\u00e2\u0080\u0094"
    response = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": f"API process {mojibake_dash} worker",
            "decision_text": f"Split API {mojibake_dash} worker for reliability.",
            "importance": 0.9,
        },
    )
    assert response.status_code == 200, response.text

    listed = client.post(
        "/memory/list_decisions",
        json={"workspace_id": "default", "query": "API worker", "limit": 1},
    )

    assert listed.status_code == 200, listed.text
    item = listed.json()["decisions"][0]
    assert item["title"] == "API process \u2014 worker"
    assert item["decision_text"] == "Split API \u2014 worker for reliability."
