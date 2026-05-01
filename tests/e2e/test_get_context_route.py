"""End-to-end tests for /memory/get_context."""

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
        "/memory/ingest_episode",
        json={
            "workspace_id": "default",
            "task_id": "phase-2-e2e",
            "source_type": "agent_action",
            "raw_text": text,
            "trust_level": "agent_observed",
            "importance": 0.7,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def test_get_context_returns_xml_envelope(client: TestClient) -> None:
    _ingest(client, "Phase 2 wires hybrid retrieval through LanceDB and FTS.")
    response = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "default",
            "query": "hybrid retrieval LanceDB",
            "max_tokens": 1500,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "<memory_context>" in body["context_text"]
    assert "<retrieved_chunks>" in body["context_text"]
    assert body["budget_diagnostics"]["max_tokens"] == 1500
    assert body["budget_diagnostics"]["estimated_tokens"] <= 1500


def test_get_context_surfaces_recent_episode(client: TestClient) -> None:
    ingested = _ingest(client, "Implemented the FTS+vector RRF fusion module.")
    chunk_id = ingested["chunk_id"]

    response = client.post(
        "/memory/get_context",
        json={"workspace_id": "default", "query": "RRF fusion module"},
    )
    assert response.status_code == 200
    body = response.json()
    source_ids = [src["id"] for src in body["sources"]]
    assert chunk_id in source_ids


def test_get_context_empty_when_workspace_unknown(client: TestClient) -> None:
    _ingest(client, "Just one episode in default.")
    response = client.post(
        "/memory/get_context",
        json={"workspace_id": "other", "query": "anything"},
    )
    assert response.status_code == 200
    assert response.json()["sources"] == []


def test_get_context_limits_decisions_by_query(client: TestClient) -> None:
    for index in range(12):
        response = client.post(
            "/memory/write_decision",
            json={
                "workspace_id": "default",
                "title": f"General operating decision {index}",
                "decision_text": "Routine operational preference.",
                "importance": 0.9,
            },
        )
        assert response.status_code == 200, response.text
    target = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "Research agenda stays visible",
            "decision_text": "Research agenda and active theories must not be buried by old decisions.",
            "importance": 0.5,
        },
    )
    assert target.status_code == 200, target.text

    response = client.post(
        "/memory/get_context",
        json={"workspace_id": "default", "query": "research agenda active theories"},
    )

    assert response.status_code == 200, response.text
    text = response.json()["context_text"]
    assert "Research agenda stays visible" in text
    assert text.count("<decision ") <= 4


def test_get_context_keeps_full_text_for_top_decisions(client: TestClient) -> None:
    tail_marker = "CRITICAL_DECISION_TAIL_event_loop_watchdog_cache_backed_health"
    decision = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "Event loop watchdog decision",
            "decision_text": " ".join(["The HTTP path must stay responsive."] * 30)
            + f" {tail_marker}",
            "rationale": "The operational agent needs the end of the decision, not only a clipped prefix.",
            "importance": 0.95,
        },
    )
    assert decision.status_code == 200, decision.text

    response = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "default",
            "query": "event loop watchdog cache backed health",
            "max_tokens": 3000,
        },
    )

    assert response.status_code == 200, response.text
    text = response.json()["context_text"]
    assert tail_marker in text
    assert 'full_text="true"' in text


def test_get_context_applies_budget_to_structured_sections(client: TestClient) -> None:
    long_text = " ".join(["structured context budget should stay bounded"] * 80)
    for index in range(8):
        response = client.post(
            "/memory/write_decision",
            json={
                "workspace_id": "default",
                "title": f"Budget pressure decision {index}",
                "decision_text": long_text,
                "importance": 0.9,
            },
        )
        assert response.status_code == 200, response.text

    role = client.post(
        "/memory/upsert_agent_role",
        json={
            "workspace_id": "default",
            "name": "Budget pressure role",
            "purpose": long_text,
            "responsibilities": [long_text, long_text],
            "confidence": 0.9,
        },
    )
    assert role.status_code == 200, role.text

    context = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "default",
            "query": "budget pressure structured context",
            "max_tokens": 600,
        },
    )
    assert context.status_code == 200, context.text
    text = context.json()["context_text"]
    assert estimate_tokens(text) <= 600
    assert text.count("<decision ") <= 2
    diagnostics = context.json()["budget_diagnostics"]
    assert diagnostics["sections"]
    assert diagnostics["sections"][0]["objects_omitted"] >= 1


def test_get_context_preserves_exact_fts_hit_under_structured_pressure(
    client: TestClient,
) -> None:
    exact = _ingest(
        client,
        "heap_watchdog v2 fixed heap pressure snapshots after Mapping source-map accumulation.",
    )
    long_text = " ".join(["structured sections should not bury exact retrieved chunks"] * 80)
    for index in range(10):
        response = client.post(
            "/memory/write_decision",
            json={
                "workspace_id": "default",
                "title": f"Structured pressure decision {index}",
                "decision_text": long_text,
                "importance": 0.95,
            },
        )
        assert response.status_code == 200, response.text

    response = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "default",
            "query": "heap_watchdog Mapping source-map heap pressure",
            "max_tokens": 900,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert exact["chunk_id"] in [source["id"] for source in body["sources"]]
    assert "heap_watchdog v2" in body["context_text"]
    assert estimate_tokens(body["context_text"]) <= 900


def test_explain_context_reports_included_and_scored_candidates(client: TestClient) -> None:
    ingested = _ingest(client, "Context explainability should show why this exact token wins.")

    response = client.post(
        "/memory/explain_context",
        json={
            "workspace_id": "default",
            "query": "context explainability exact token",
            "max_tokens": 1200,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workspace_id"] == "default"
    assert body["context_tokens"] <= body["max_tokens"]
    assert ingested["chunk_id"] in body["included_ids"]
    scored = {item["id"]: item for item in body["scored_candidates"]}
    assert scored[ingested["chunk_id"]]["included"] is True
    assert scored[ingested["chunk_id"]]["reason"] == "included_in_context"


def test_explain_context_reports_suppressed_behavior_instructions(
    client: TestClient,
) -> None:
    expired = client.post(
        "/memory/upsert_behavior_instruction",
        json={
            "workspace_id": "default",
            "name": "Expired incident style",
            "kind": "communication_style",
            "scope": "workspace",
            "priority": "user_preference",
            "rule": "Use a stale incident report style.",
            "conflict_policy": "current_user_wins",
            "expires_at": "2000-01-01T00:00:00+00:00",
            "confidence": 0.9,
        },
    )
    assert expired.status_code == 200, expired.text

    context = client.post(
        "/memory/get_context",
        json={"workspace_id": "default", "query": "incident style"},
    )
    assert context.status_code == 200, context.text
    assert "Expired incident style" not in context.json()["context_text"]

    explained = client.post(
        "/memory/explain_context",
        json={"workspace_id": "default", "query": "incident style"},
    )
    assert explained.status_code == 200, explained.text
    suppressed = explained.json()["suppressed_behavior_instructions"]
    assert suppressed == [
        {
            "id": expired.json()["instruction_id"],
            "name": "Expired incident style",
            "reason": "expired",
            "details": {"expires_at": "2000-01-01T00:00:00+00:00"},
        }
    ]
