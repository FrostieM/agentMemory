from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agent_memory_lite import __version__


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_memory_status_returns_well_formed_payload(client: TestClient) -> None:
    """Empty workspace: every count is zero, ratios are zero, last_*_at is None.
    Smoke that the schema accepts a fresh DB without crashing on
    empty tables or missing code-memory tables."""
    r = client.get("/memory/status", params={"workspace_id": "default"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == __version__
    assert body["workspace_id"] == "default"
    mem = body["memory"]
    assert mem["decisions_active"] == 0
    assert mem["episodes_total"] == 0
    code = body["code_memory"]
    assert code["files"] == 0
    assert code["symbols"] == 0
    adoption = body["adoption"]
    assert adoption["decisions_with_source_episode_ratio"] == 0.0
    assert adoption["behavior_instructions_fired_ratio"] == 0.0
    assert body["last_episode_at"] is None
    assert body["recent_actions_7d"] == {}


def test_memory_status_reflects_writes(client: TestClient) -> None:
    """After write_decision + ingest_episode, counts + adoption ratios reflect
    the new state. Move 1 auto-thread should bump
    decisions_with_source_episode_ratio toward 1.0."""
    headers = {"X-Memory-Agent-Id": "claude-test"}
    ep = client.post(
        "/memory/ingest_episode",
        headers=headers,
        json={
            "workspace_id": "default",
            "raw_text": "memory_status route smoke evidence",
            "source_type": "agent_action",
            "trust_level": "agent_observed",
            "importance": 0.6,
        },
    )
    assert ep.status_code == 200, ep.text
    dec = client.post(
        "/memory/write_decision",
        headers=headers,
        json={
            "workspace_id": "default",
            "title": "memory_status smoke",
            "decision_text": "Status smoke decision used to verify counts.",
        },
    )
    assert dec.status_code == 200, dec.text

    r = client.get("/memory/status", params={"workspace_id": "default"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["memory"]["decisions_active"] == 1
    assert body["memory"]["episodes_total"] >= 1
    assert body["adoption"]["decisions_with_source_episode_ratio"] == 1.0
    assert body["last_episode_at"] is not None
    assert body["last_decision_at"] is not None
