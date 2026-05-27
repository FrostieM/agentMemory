from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_write_and_compact_search_render_theory(client: TestClient) -> None:
    write = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "theory",
            "payload": {
                "title": "Source-flip tennis favorites",
                "domain": "trading.paper.edge",
                "claim": "Source-flip trades on tennis favorites may have positive edge.",
                "mechanism": "The source wallet may react before the public odds fully adjust.",
                "predictions": ["favorite-side flips outperform underdog-side flips"],
                "validation_criteria": [
                    "minimum 100 settled trades",
                    "positive net edge after fees",
                ],
                "experiment_plan": "Replay source-flip fills by sport and side.",
                "tags": ["trading-bot", "source-flip", "tennis", "favorite"],
                "status": "testing",
                "confidence": 0.3,
                "importance": 0.9,
            },
        },
    )
    assert write.status_code == 200, write.text
    theory_id = write.json()["data"]["theory_id"]
    assert theory_id.startswith("th_")
    assert write.json()["data"]["validation_criteria"] == [
        "minimum 100 settled trades",
        "positive net edge after fees",
    ]

    fetched = client.get(
        "/memory/get",
        params={
            "workspace_id": "default",
            "kind": "theory",
            "id": theory_id,
            "fields": "claim,validation_criteria",
        },
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"]["id"] == theory_id
    assert "positive edge" in fetched.json()["data"]["claim"]

    context = client.post(
        "/memory/search",
        json={
            "workspace_id": "default",
            "query": "should we study tennis source-flip favorite edge",
            "kinds": ["theory"],
            "limit": 5,
        },
    )
    assert context.status_code == 200, context.text
    text = str(context.json()["data"])
    assert "Source-flip tennis favorites" in text


# ---------- Capability suggestions on canonical theory write ----------


def test_write_theory_returns_capability_suggestions(client: TestClient) -> None:
    """When a workspace skill token-overlaps the theory's title + claim +
    mechanism, canonical theory writes surface it in capability_suggestions.
    Mirrors the Move 3 hint on canonical writes."""
    skill = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "skill",
            "payload": {
                "name": "Source-flip replay",
                "summary": "tennis source-flip replay cohort design",
                "body_md": "tennis source-flip replay cohort design",
                "when_to_use_short": "replay source-flip trades",
                "subtype": "skill",
                "active": True,
                "confidence": 0.8,
            },
        },
    )
    assert skill.status_code == 200, skill.text

    response = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "theory",
            "payload": {
                "title": "Source-flip tennis favorites",
                "domain": "trading.paper.edge",
                "claim": "Source-flip trades on tennis favorites may have positive edge.",
                "mechanism": "Source wallet reacts before the public odds fully adjust.",
                "predictions": ["favorite-side outperforms underdog-side"],
                "validation_criteria": ["minimum 100 settled trades"],
                "tags": ["trading-bot", "source-flip"],
                "status": "testing",
                "confidence": 0.3,
                "importance": 0.9,
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    suggestions = body["capability_suggestions"]
    assert isinstance(suggestions, list)
    assert len(suggestions) >= 1
    top = suggestions[0]
    assert top["capability_type"] == "skill"
    assert top["capability_name"] == "Source-flip replay"
    assert top["score"] > 0.0
    assert top["snippet"]


def test_write_theory_returns_empty_suggestions_when_no_capabilities(
    client: TestClient,
) -> None:
    """No canonical skill in the workspace ->
    capability_suggestions is an empty list, not missing or null."""
    response = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "theory",
            "payload": {
                "title": "Isolated theory",
                "domain": "general",
                "claim": "Edge case with no capabilities seeded.",
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["capability_suggestions"] == []
