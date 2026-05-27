"""E2E for /memory/cold_decisions (1.3.0)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="cold-ws")
    with TestClient(app) as c:
        yield c


def test_cold_decisions_empty_workspace(client: TestClient) -> None:
    """No decisions → empty rows + total_active=0."""
    r = client.get("/memory/cold_decisions?workspace_id=cold-ws")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_active"] == 0
    assert body["cold_count"] == 0
    assert body["rows"] == []


def test_cold_decisions_lists_never_retrieved(client: TestClient) -> None:
    """A freshly-written decision has never been retrieved → days_cold=9999."""
    write = client.post(
        "/memory/write",
        json={
            "workspace_id": "cold-ws",
            "kind": "decision",
            "payload": {
                "title": "Brand new",
                "decision_text": "Recently added; never retrieved.",
                "importance": 0.85,
            },
        },
    )
    assert write.status_code == 200, write.text
    r = client.get("/memory/cold_decisions?workspace_id=cold-ws&cutoff_days=1")
    body = r.json()
    assert body["cold_count"] == 1
    row = body["rows"][0]
    assert row["title"] == "Brand new"
    assert row["days_cold"] == 9999
    assert row["last_retrieved_at"] is None


def test_cold_decisions_validates_cutoff_range(client: TestClient) -> None:
    r = client.get("/memory/cold_decisions?workspace_id=cold-ws&cutoff_days=0")
    assert r.status_code == 422
    r = client.get("/memory/cold_decisions?workspace_id=cold-ws&cutoff_days=400")
    assert r.status_code == 422
