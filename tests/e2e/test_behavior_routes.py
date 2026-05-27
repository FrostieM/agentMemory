from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_behavior_routes_feed_compact_brief(client: TestClient) -> None:
    created = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "behavior",
            "payload": {
                "name": "Direct technical communication",
                "kind": "communication_style",
                "scope": "workspace",
                "priority": "user_preference",
                "rule": "Answer in a direct, evidence-first engineering style.",
                "rationale": "The user prefers concrete issue/evidence/fix/risk reports.",
                "applies_to": ["status updates", "incident reports"],
                "conflict_policy": "current_user_wins",
                "source_type": "user_direct",
                "source_id": "chat-123",
                "reviewed_by": "operator",
                "reviewed_at": "2026-04-30T00:00:00+00:00",
                "expires_at": "2999-01-01T00:00:00+00:00",
                "conflict_group": "incident-report-style",
                "confidence": 0.95,
            },
        },
    )
    assert created.status_code == 200, created.text
    behavior_id = created.json()["data"]["instruction_id"]
    assert behavior_id.startswith("beh_")

    listed = client.post(
        "/memory/search",
        json={
            "workspace_id": "default",
            "query": "direct evidence communication",
            "kinds": ["behavior"],
            "limit": 5,
        },
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()["data"]
    assert body[0]["projection"]["name"] == "Direct technical communication"
    full = client.get(
        "/memory/get",
        params={
            "workspace_id": "default",
            "kind": "behavior",
            "id": body[0]["projection"]["id"],
            "fields": "conflict_policy,source_type,source_id",
        },
    ).json()
    assert full["data"]["conflict_policy"] == "current_user_wins"
    assert full["data"]["source_type"] == "user_direct"
    assert full["data"]["source_id"] == "chat-123"

    pinned = client.post(
        "/memory/pin",
        json={"workspace_id": "default", "kind": "behavior", "id": behavior_id, "pinned": True},
    )
    assert pinned.status_code == 200, pinned.text

    context = client.get(
        "/memory/brief",
        params={
            "workspace_id": "default",
            "task": "unrelated runtime investigation",
            "max_tokens": 2000,
        },
    )
    assert context.status_code == 200, context.text
    text = context.json()["data"]["body_md"]
    assert "Direct technical communication" in text
