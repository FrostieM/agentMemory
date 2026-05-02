from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_capability_link_makes_skill_influence_theory_retrieval(
    client: TestClient,
) -> None:
    skill = client.post(
        "/memory/upsert_agent_skill",
        json={
            "workspace_id": "default",
            "name": "Replay and backtest design",
            "summary": "Design controlled replay experiments and leakage-safe backtests.",
            "when_to_use": ["A hypothesis must be validated before a policy change"],
            "confidence": 0.94,
        },
    )
    assert skill.status_code == 200, skill.text

    theory = client.post(
        "/memory/write_theory",
        json={
            "workspace_id": "default",
            "title": "Sparse opens are a learning bottleneck",
            "domain": "paper.admission",
            "claim": "Admission policy may be too sparse to learn from outcomes.",
            "mechanism": "Several individually reasonable gates can collapse sample volume.",
            "validation_criteria": ["measure open rate and realized PnL under controlled variants"],
            "status": "testing",
            "confidence": 0.5,
            "importance": 0.9,
        },
    )
    assert theory.status_code == 200, theory.text
    theory_id = theory.json()["theory_id"]

    link = client.post(
        "/memory/link_capability",
        json={
            "workspace_id": "default",
            "target_type": "theory",
            "target_id": theory_id,
            "capability_type": "skill",
            "capability_name": "Replay and backtest design",
            "relation": "method",
            "rationale": "This theory must be tested with replay before admission changes.",
            "strength": 0.91,
        },
    )
    assert link.status_code == 200, link.text
    assert link.json()["capability_name"] == "Replay and backtest design"

    listed = client.post(
        "/memory/list_theories",
        json={"workspace_id": "default", "query": "backtest leakage replay"},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["theories"][0]["theory"]["theory_id"] == theory_id

    links = client.post(
        "/memory/list_capability_links",
        json={"workspace_id": "default", "target_type": "theory", "target_id": theory_id},
    )
    assert links.status_code == 200, links.text
    assert links.json()["links"][0]["relation"] == "method"

    context = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "default",
            "query": "which hypothesis needs replay backtest design",
            "max_tokens": 1800,
        },
    )
    assert context.status_code == 200, context.text
    text = context.json()["context_text"]
    assert "Sparse opens are a learning bottleneck" in text
    assert "<capability_links>" in text
    assert "Replay and backtest design" in text
    assert "This theory must be tested with replay" in text
