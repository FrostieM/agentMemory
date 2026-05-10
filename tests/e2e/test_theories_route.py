from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_write_list_and_context_render_theory(client: TestClient) -> None:
    write = client.post(
        "/memory/write_theory",
        json={
            "workspace_id": "default",
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
    )
    assert write.status_code == 200, write.text
    theory_id = write.json()["theory_id"]
    assert theory_id.startswith("th_")
    assert write.json()["validation_criteria"] == [
        "minimum 100 settled trades",
        "positive net edge after fees",
    ]

    evidence = client.post(
        "/memory/add_theory_evidence",
        json={
            "workspace_id": "default",
            "theory_id": theory_id,
            "kind": "experiment",
            "summary": "Replay cohort query is ready for the latest VPS snapshot.",
            "artifact_path": "research/snapshots/server_20260427T105823/research.duckdb",
            "metrics": {"planned": True},
        },
    )
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["theory_id"] == theory_id

    listed = client.post(
        "/memory/list_theories",
        json={
            "workspace_id": "default",
            "query": "tennis source-flip favorite",
            "include_evidence": True,
        },
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["theories"][0]["theory"]["theory_id"] == theory_id
    assert body["theories"][0]["theory"]["evidence_count"] == 1
    assert body["theories"][0]["evidence"][0]["kind"] == "experiment"

    context = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "default",
            "query": "should we study tennis source-flip favorite edge",
            "max_tokens": 1500,
        },
    )
    assert context.status_code == 200, context.text
    text = context.json()["context_text"]
    assert "<active_theories>" in text
    assert "Source-flip tennis favorites" in text
    assert "minimum 100 settled trades" in text
    assert "Replay cohort query is ready" in text


def test_add_evidence_unknown_theory_returns_404(client: TestClient) -> None:
    response = client.post(
        "/memory/add_theory_evidence",
        json={
            "workspace_id": "default",
            "theory_id": "th_missing",
            "kind": "supporting",
            "summary": "not attached",
        },
    )
    assert response.status_code == 404


# ---------- 2.2 Move 4: capability suggestions on write_theory ----------


def test_write_theory_returns_capability_suggestions(client: TestClient) -> None:
    """When a workspace skill token-overlaps the theory's title + claim +
    mechanism, write_theory's response surfaces it in capability_suggestions.
    Mirrors the Move 3 hint on write_decision / record_with_evidence."""
    skill = client.post(
        "/memory/upsert_agent_skill",
        json={
            "workspace_id": "default",
            "name": "Source-flip replay",
            "summary": "tennis source-flip replay cohort design",
            "when_to_use": ["replay source-flip trades"],
            "tools": [],
        },
    )
    assert skill.status_code == 200, skill.text

    response = client.post(
        "/memory/write_theory",
        json={
            "workspace_id": "default",
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
    )
    assert response.status_code == 200, response.text
    body = response.json()
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
    """No agent_skill / agent_role / agent_playbook in the workspace ->
    capability_suggestions is an empty list, not missing or null."""
    response = client.post(
        "/memory/write_theory",
        json={
            "workspace_id": "default",
            "title": "Isolated theory",
            "domain": "general",
            "claim": "Edge case with no capabilities seeded.",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["capability_suggestions"] == []
