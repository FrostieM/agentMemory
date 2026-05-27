from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_research_routes_feed_compact_agenda(client: TestClient) -> None:
    theory = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "theory",
            "payload": {
                "title": "Sparse paper opens",
                "claim": "Sparse paper opens are a learning bottleneck.",
                "status": "testing",
                "confidence": 0.45,
                "tags": ["trading-bot", "paper"],
            },
        },
    )
    assert theory.status_code == 200, theory.text
    theory_id = theory.json()["data"]["theory_id"]

    snapshot = client.post(
        "/memory/register_snapshot",
        json={
            "workspace_id": "default",
            "snapshot_key": "server_20260427T105823",
            "title": "VPS database snapshot before reset",
            "source": "vps",
            "duckdb_path": "research/snapshots/server_20260427T105823/research.duckdb",
            "table_counts": {"trade_decision_fact": 49452, "bot_paper_positions": 191},
            "total_rows": 499141,
        },
    )
    assert snapshot.status_code == 200, snapshot.text
    snapshot_id = snapshot.json()["snapshot_id"]
    assert snapshot_id.startswith("snap_")

    concept = client.post(
        "/memory/upsert_concept",
        json={
            "workspace_id": "default",
            "name": "selector-gate",
            "kind": "gate",
            "definition": "Admission rule that prevents a candidate from reaching paper.",
            "tags": ["trading-bot", "selector"],
        },
    )
    assert concept.status_code == 200, concept.text

    insight = client.post(
        "/memory/distill_insight",
        json={
            "workspace_id": "default",
            "insight_type": "risk",
            "summary": "Unlinked insight should be attachable without direct DB edits.",
            "proposed_action": "Link the insight to the sparse-open theory.",
            "confidence": 0.85,
        },
    )
    assert insight.status_code == 200, insight.text
    insight_id = insight.json()["insight_id"]

    updated_insight = client.post(
        "/memory/update_insight",
        json={
            "workspace_id": "default",
            "insight_id": insight_id,
            "target_type": "theory",
            "target_id": theory_id,
            "status": "accepted",
        },
    )
    assert updated_insight.status_code == 200, updated_insight.text
    updated_body = updated_insight.json()
    assert updated_body["target_type"] == "theory"
    assert updated_body["target_id"] == theory_id
    assert updated_body["status"] == "accepted"

    experiment = client.post(
        "/memory/write_experiment",
        json={
            "workspace_id": "default",
            "theory_id": theory_id,
            "snapshot_id": snapshot_id,
            "title": "Soft-gate replay for paper-open-rate",
            "hypothesis": "A softer selector gate increases observable paper learning data.",
            "cohort_definition": "shadow candidates with skip-real-trade-selector reasons",
            "success_criteria": {"paper_open_rate_gt": 0.02, "max_drawdown_bps_lt": 120},
            "priority": 0.95,
        },
    )
    assert experiment.status_code == 200, experiment.text
    experiment_id = experiment.json()["experiment_id"]

    result = client.post(
        "/memory/add_experiment_result",
        json={
            "workspace_id": "default",
            "experiment_id": experiment_id,
            "kind": "supporting",
            "summary": "Soft-gate replay should be run before another overnight wait.",
            "metrics": {"planned": True},
            "confidence": 0.7,
        },
    )
    assert result.status_code == 200, result.text

    fetched_snapshot = client.get(
        "/memory/get",
        params={"workspace_id": "default", "kind": "snapshot", "id": snapshot_id},
    )
    assert fetched_snapshot.status_code == 200, fetched_snapshot.text
    assert fetched_snapshot.json()["data"]["snapshot_key"] == "server_20260427T105823"

    context = client.post(
        "/memory/search",
        json={
            "workspace_id": "default",
            "query": "paper selector gate open-rate research agenda",
            "kinds": ["concept", "theory", "insight", "snapshot"],
            "limit": 10,
        },
    )
    assert context.status_code == 200, context.text
    text = str(context.json()["data"])
    assert "selector-gate" in text
    assert "server_20260427T105823" in text
