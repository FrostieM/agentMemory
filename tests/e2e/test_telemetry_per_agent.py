"""E2E for the v1.3.0 per-agent telemetry partition + search hit-rate."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="agent-tel-ws")
    with TestClient(app) as c:
        yield c


def test_telemetry_partitions_by_agent_id_header(client: TestClient) -> None:
    """1.3.0: X-Memory-Agent-Id header attributes audit rows to a
    specific agent. The telemetry endpoint surfaces a per-agent
    breakdown so Claude vs Codex search-rates can be measured
    separately."""
    # ingests as "agent-A"
    for i in range(2):
        r = client.post(
            "/memory/ingest_episode",
            json={
                "workspace_id": "agent-tel-ws",
                "source_type": "agent_action",
                "raw_text": f"a-event-{i} body content for telemetry test",
                "trust_level": "agent_observed",
            },
            headers={"X-Memory-Agent-Id": "agent-A"},
        )
        assert r.status_code == 200, r.text
    # search as "agent-B"
    for q in ("event", "telemetry", "body"):
        r = client.post(
            "/memory/search",
            json={"workspace_id": "agent-tel-ws", "query": q, "limit": 5},
            headers={"X-Memory-Agent-Id": "agent-B"},
        )
        assert r.status_code == 200, r.text

    r = client.get("/memory/telemetry?workspace_id=agent-tel-ws&days=1")
    assert r.status_code == 200
    body = r.json()
    by_agent = {row["agent_id"]: row for row in body["by_agent"]}
    assert "agent-A" in by_agent
    assert "agent-B" in by_agent
    assert by_agent["agent-A"]["write"] >= 2
    assert by_agent["agent-A"]["search"] == 0
    assert by_agent["agent-B"]["search"] >= 3
    assert by_agent["agent-B"]["write"] == 0


def test_telemetry_search_hit_rate_present(client: TestClient) -> None:
    """1.3.0: search_hit_rate, search_calls_with_hits, search_calls_zero_hits
    surface in the response. With no episodes, all searches are zero-hits."""
    for q in ("nothing-to-find-1", "nothing-to-find-2"):
        client.post(
            "/memory/search",
            json={"workspace_id": "agent-tel-ws", "query": q, "limit": 5},
        )
    r = client.get("/memory/telemetry?workspace_id=agent-tel-ws&days=1")
    body = r.json()
    assert "search_hit_rate" in body
    assert "search_calls_with_hits" in body
    assert "search_calls_zero_hits" in body
    # With at least the two zero-hit searches we just made:
    assert body["search_calls_zero_hits"] >= 2


def test_telemetry_unknown_agent_id_bucket(client: TestClient) -> None:
    """When the header is absent, audit rows have agent_id=NULL and the
    telemetry response groups them into ``(unknown)``."""
    client.post(
        "/memory/ingest_episode",
        json={
            "workspace_id": "agent-tel-ws",
            "source_type": "agent_action",
            "raw_text": "unattributed event for unknown bucket test",
            "trust_level": "agent_observed",
        },
    )
    r = client.get("/memory/telemetry?workspace_id=agent-tel-ws&days=1")
    body = r.json()
    agent_ids = {row["agent_id"] for row in body["by_agent"]}
    assert "(unknown)" in agent_ids
