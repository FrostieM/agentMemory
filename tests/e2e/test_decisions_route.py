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
