from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_legacy_capability_link_routes_are_not_active_surface(
    client: TestClient,
) -> None:
    skill = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "skill",
            "payload": {
                "name": "Replay and backtest design",
                "summary": "Design controlled replay experiments and leakage-safe backtests.",
                "body_md": "Design controlled replay experiments and leakage-safe backtests.",
                "trigger": "A hypothesis must be validated before a policy change.",
                "active": True,
                "confidence": 0.94,
            },
        },
    )
    assert skill.status_code == 200, skill.text

    theory = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "theory",
            "payload": {
                "title": "Sparse opens are a learning bottleneck",
                "domain": "paper.admission",
                "claim": "Admission policy may be too sparse to learn from outcomes.",
                "mechanism": "Several individually reasonable gates can collapse sample volume.",
                "validation_criteria": [
                    "measure open rate and realized PnL under controlled variants"
                ],
                "status": "testing",
                "confidence": 0.5,
                "importance": 0.9,
            },
        },
    )
    assert theory.status_code == 200, theory.text

    skill_search = client.post(
        "/memory/search",
        json={
            "workspace_id": "default",
            "query": "replay backtest design",
            "kinds": ["skill"],
            "limit": 5,
        },
    )
    assert skill_search.status_code == 200, skill_search.text
    text = str(skill_search.json()["data"])
    assert "Replay and backtest design" in text

    theory_search = client.post(
        "/memory/search",
        json={
            "workspace_id": "default",
            "query": "sparse admission learning bottleneck",
            "kinds": ["theory"],
            "limit": 5,
        },
    )
    assert theory_search.status_code == 200, theory_search.text
    assert "Sparse opens are a learning bottleneck" in str(theory_search.json()["data"])
