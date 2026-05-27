"""End-to-end tests for the v3 compact read surface."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agent_memory_lite.utils.tokens import estimate_tokens


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, text: str) -> dict[str, object]:
    response = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "episode",
            "payload": {
                "task_id": "phase-2-e2e",
                "source_type": "agent_action",
                "raw_text": text,
                "trust_level": "agent_observed",
                "importance": 0.7,
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body["data"]


def _write_decision(client: TestClient, **payload):
    return client.post(
        "/memory/write",
        json={"workspace_id": "default", "kind": "decision", "payload": payload},
    )


def _write_behavior(client: TestClient, **payload):
    return client.post(
        "/memory/write",
        json={"workspace_id": "default", "kind": "behavior", "payload": payload},
    )


def test_brief_returns_budgeted_markdown(client: TestClient) -> None:
    _ingest(client, "Phase 2 wires hybrid retrieval through LanceDB and FTS.")
    response = client.get(
        "/memory/brief",
        params={
            "workspace_id": "default",
            "task": "hybrid retrieval LanceDB",
            "max_tokens": 500,
        },
    )
    assert response.status_code == 200
    body = response.json()["data"]["body_md"]
    assert body.strip()
    assert estimate_tokens(body) <= 500


def test_search_surfaces_recent_episode(client: TestClient) -> None:
    ingested = _ingest(client, "Implemented the FTS+vector RRF fusion module.")
    chunk_id = ingested["chunk_id"]

    response = client.post(
        "/memory/search",
        json={"workspace_id": "default", "query": "RRF fusion module", "limit": 8},
    )
    assert response.status_code == 200
    hits = response.json()["data"]
    source_ids = [hit["projection"]["id"] for hit in hits]
    assert chunk_id in source_ids


def test_search_empty_when_workspace_unknown(client: TestClient) -> None:
    _ingest(client, "Just one episode in default.")
    response = client.post(
        "/memory/search",
        json={"workspace_id": "other", "query": "anything", "limit": 8},
    )
    assert response.status_code == 200
    assert response.json()["data"] == []


def test_search_limits_decisions_by_query(client: TestClient) -> None:
    for index in range(12):
        response = _write_decision(
            client,
            title=f"General operating decision {index}",
            decision_text="Routine operational preference.",
            importance=0.9,
        )
        assert response.status_code == 200, response.text
    target = _write_decision(
        client,
        title="Research agenda stays visible",
        decision_text="Research agenda and active theories must not be buried by old decisions.",
        importance=0.5,
    )
    assert target.status_code == 200, target.text

    response = client.post(
        "/memory/search",
        json={
            "workspace_id": "default",
            "query": "research agenda active theories",
            "kinds": ["decision"],
            "limit": 5,
        },
    )

    assert response.status_code == 200, response.text
    text = str(response.json())
    assert "Research agenda stays visible" in text
    assert text.count("General operating decision") < 12


def test_get_fetches_full_decision_fields(client: TestClient) -> None:
    tail_marker = "CRITICAL_DECISION_TAIL_event_loop_watchdog_cache_backed_health"
    decision = _write_decision(
        client,
        title="Event loop watchdog decision",
        decision_text=" ".join(["The HTTP path must stay responsive."] * 30) + f" {tail_marker}",
        rationale="The operational agent needs the end of the decision, not only a clipped prefix.",
        importance=0.95,
    )
    assert decision.status_code == 200, decision.text
    decision_id = decision.json()["data"]["id"]

    response = client.get(
        "/memory/get",
        params={
            "workspace_id": "default",
            "kind": "decision",
            "id": decision_id,
            "fields": "decision_text,rationale",
        },
    )

    assert response.status_code == 200, response.text
    text = str(response.json()["data"])
    assert tail_marker in text


def test_search_preserves_exact_hit_under_structured_pressure(client: TestClient) -> None:
    exact = _ingest(
        client,
        "heap_watchdog v3 fixed heap pressure snapshots after Mapping source-map accumulation.",
    )
    long_text = " ".join(["structured sections should not bury exact retrieved chunks"] * 80)
    for index in range(10):
        response = _write_decision(
            client,
            title=f"Structured pressure decision {index}",
            decision_text=long_text,
            importance=0.95,
        )
        assert response.status_code == 200, response.text

    response = client.post(
        "/memory/search",
        json={
            "workspace_id": "default",
            "query": "heap_watchdog Mapping source-map heap pressure",
            "limit": 8,
        },
    )

    assert response.status_code == 200, response.text
    hits = response.json()["data"]
    assert exact["chunk_id"] in [hit["projection"]["id"] for hit in hits]


def test_expired_behavior_is_not_returned_as_active(client: TestClient) -> None:
    expired = _write_behavior(
        client,
        name="Expired incident style",
        kind="communication_style",
        scope="workspace",
        priority="user_preference",
        rule="Use a stale incident report style.",
        conflict_policy="current_user_wins",
        expires_at="2000-01-01T00:00:00+00:00",
        confidence=0.9,
    )
    assert expired.status_code == 200, expired.text

    response = client.get(
        "/memory/brief",
        params={
            "workspace_id": "default",
            "task": "incident style",
            "max_tokens": 800,
        },
    )
    assert response.status_code == 200, response.text
    assert "Expired incident style" not in response.json()["data"]["body_md"]
