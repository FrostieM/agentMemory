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
