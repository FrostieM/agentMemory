"""E2E for the v1.7.0 active-edit registry."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="coord-ws")
    with TestClient(app) as c:
        yield c


def test_claim_release_round_trip(client: TestClient) -> None:
    r = client.post(
        "/memory/claim_edit",
        json={
            "workspace_id": "coord-ws",
            "agent_id": "claude",
            "qualified_name": "paperBot.calculate",
        },
    )
    assert r.status_code == 200, r.text
    claim = r.json()
    assert claim["agent_id"] == "claude"
    assert claim["qualified_name"] == "paperBot.calculate"
    claim_id = claim["id"]

    # Listing shows the claim
    r2 = client.post(
        "/memory/list_active_edits",
        json={"workspace_id": "coord-ws"},
    )
    assert r2.status_code == 200, r2.text
    assert any(e["id"] == claim_id for e in r2.json()["edits"])

    # Release succeeds
    r3 = client.post(
        "/memory/release_edit",
        json={"workspace_id": "coord-ws", "claim_id": claim_id},
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["released"] is True

    # No longer in the list
    r4 = client.post(
        "/memory/list_active_edits",
        json={"workspace_id": "coord-ws"},
    )
    assert all(e["id"] != claim_id for e in r4.json()["edits"])


def test_other_agent_blocked_by_409(client: TestClient) -> None:
    """Two agents try to claim the same target — second one gets 409."""
    body = {
        "workspace_id": "coord-ws",
        "agent_id": "alice",
        "qualified_name": "shared.target",
    }
    assert client.post("/memory/claim_edit", json=body).status_code == 200

    r = client.post(
        "/memory/claim_edit",
        json={**body, "agent_id": "bob"},
    )
    assert r.status_code == 409, r.text
    assert "alice" in r.text


def test_same_agent_can_re_claim(client: TestClient) -> None:
    """Idempotent re-claim by the same agent is allowed."""
    body = {
        "workspace_id": "coord-ws",
        "agent_id": "alice",
        "qualified_name": "alice.target",
        "ttl_minutes": 5,
    }
    r1 = client.post("/memory/claim_edit", json=body)
    assert r1.status_code == 200
    r2 = client.post("/memory/claim_edit", json={**body, "ttl_minutes": 60, "note": "extending"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["note"] == "extending"


def test_claim_requires_target(client: TestClient) -> None:
    r = client.post(
        "/memory/claim_edit",
        json={"workspace_id": "coord-ws", "agent_id": "claude"},
    )
    assert r.status_code == 400, r.text
