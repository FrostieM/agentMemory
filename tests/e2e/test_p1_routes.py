from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="project-a")
    with TestClient(app) as c:
        yield c


def _write_decision(client: TestClient, title: str, text: str) -> str:
    response = client.post(
        "/memory/write",
        json={
            "workspace_id": "project-a",
            "kind": "decision",
            "payload": {"title": title, "decision_text": text},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    return body["data"]["id"]


def test_pin_route_flips_decision(client: TestClient) -> None:
    decision_id = _write_decision(
        client, "Architectural invariant: local-only", "Never call cloud LLMs."
    )
    pinned = client.post(
        "/memory/pin",
        json={"workspace_id": "project-a", "kind": "decision", "id": decision_id},
    )
    assert pinned.status_code == 200, pinned.text
    body = pinned.json()
    assert body["ok"] is True
    assert body["data"]["kind"] == "decision"
    assert body["data"]["id"] == decision_id
    assert body["data"]["pinned"] is True

    fetched = client.get(
        "/memory/get",
        params={
            "workspace_id": "project-a",
            "kind": "decision",
            "id": decision_id,
        },
    )
    assert fetched.status_code == 200
    assert fetched.json()["data"]["pinned"] is True


def test_pin_unsupported_kind_returns_error_envelope(client: TestClient) -> None:
    response = client.post(
        "/memory/pin",
        json={"workspace_id": "project-a", "kind": "theory", "id": "th_x"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unsupported_kind"


def test_pin_route_flips_behavior(client: TestClient) -> None:
    written = client.post(
        "/memory/write",
        json={
            "workspace_id": "project-a",
            "kind": "behavior",
            "payload": {
                "name": "Pinnable instruction",
                "rule": "Pinned instructions ride along regardless of query.",
                "kind": "operating_rule",
                "rationale": "Operator-critical guard rail.",
                "confidence": 0.9,
            },
        },
    )
    assert written.status_code == 200, written.text
    instruction_id = written.json()["data"]["id"]
    pinned = client.post(
        "/memory/pin",
        json={
            "workspace_id": "project-a",
            "kind": "behavior",
            "id": instruction_id,
        },
    )
    assert pinned.status_code == 200, pinned.text
    body = pinned.json()
    assert body["ok"] is True
    assert body["data"]["kind"] == "behavior"
    assert body["data"]["id"] == instruction_id
    assert body["data"]["pinned"] is True


def test_what_references_finds_decision_text(client: TestClient) -> None:
    decision_id = _write_decision(
        client, "Reverse lookup target", "This decision will be referenced elsewhere."
    )
    other_id = _write_decision(
        client,
        "Decision that mentions the previous one",
        f"This depends on {decision_id} as the upstream choice.",
    )
    response = client.post(
        "/memory/what_references",
        json={"workspace_id": "project-a", "target_id": decision_id, "limit": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target_id"] == decision_id
    found_ids = {h["id"] for h in body["hits"]}
    assert other_id in found_ids


def test_list_audit_returns_memory_write_entries(client: TestClient) -> None:
    decision_id = _write_decision(client, "Auditable decision", "Body for audit.")
    response = client.post(
        "/memory/audit",
        json={
            "workspace_id": "project-a",
            "target_type": "decision",
            "target_id": decision_id,
            "limit": 10,
        },
    )
    assert response.status_code == 200, response.text
    entries = response.json()["entries"]
    assert any(e["action"] in {"write", "write_decision"} for e in entries)
    assert all(e["target_id"] == decision_id for e in entries)
