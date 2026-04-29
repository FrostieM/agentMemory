from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_behavior_instruction_routes_feed_context(client: TestClient) -> None:
    created = client.post(
        "/memory/upsert_behavior_instruction",
        json={
            "workspace_id": "default",
            "name": "Direct technical communication",
            "kind": "communication_style",
            "scope": "workspace",
            "priority": "user_preference",
            "rule": "Answer in a direct, evidence-first engineering style.",
            "rationale": "The user prefers concrete issue/evidence/fix/risk reports.",
            "applies_to": ["status updates", "incident reports"],
            "conflict_policy": "current_user_wins",
            "confidence": 0.95,
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["instruction_id"].startswith("beh_")

    listed = client.post(
        "/memory/list_behavior_instructions",
        json={"workspace_id": "default", "query": "direct evidence communication"},
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["instructions"][0]["name"] == "Direct technical communication"
    assert body["instructions"][0]["conflict_policy"] == "current_user_wins"

    context = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "default",
            "query": "unrelated runtime investigation",
            "max_tokens": 2500,
        },
    )
    assert context.status_code == 200, context.text
    text = context.json()["context_text"]
    assert "<behavior_instructions>" in text
    assert "Direct technical communication" in text
    assert "current_user_wins" in text
