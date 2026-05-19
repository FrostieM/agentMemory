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


def test_memory_status_environment_off_by_default(client: TestClient) -> None:
    """``include_environment`` defaults to false — payload backward-compat."""
    r = client.get("/memory/status", params={"workspace_id": "default"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["environment"] is None


def test_memory_status_environment_block_opt_in(client: TestClient) -> None:
    """``include_environment=true`` surfaces anchor / hub_mode / registry."""
    r = client.get(
        "/memory/status",
        params={"workspace_id": "default", "include_environment": True},
    )
    assert r.status_code == 200, r.text
    env = r.json()["environment"]
    assert env is not None
    # Anchor fields populated even on an unregistered fresh workspace.
    assert env["anchor_workspace_id"]
    assert env["anchor_db_path"]
    assert env["anchor_vector_db_path"]
    assert isinstance(env["hub_mode"], bool)
    assert isinstance(env["forbid_default_workspace"], bool)
    assert isinstance(env["strict_workspace_isolation"], bool)
    # Registry payload is a list of ids and a path (may be empty list).
    assert isinstance(env["registry_workspaces"], list)
    assert env["registry_path"]
    # Migrations: at minimum 0001_init must have been applied.
    assert "0001_init" in env["applied_migrations"]
    # P2 runtime-stack fields populated.
    assert env["embedding_backend"]
    assert env["vector_backend"]
    assert env["http_base_url"].startswith("http://127.0.0.1:")


def test_memory_status_active_memory_default_omitted(client: TestClient) -> None:
    """``active_memory`` is opt-in — default payload has it null."""
    r = client.get("/memory/status", params={"workspace_id": "default"})
    assert r.status_code == 200, r.text
    assert r.json()["active_memory"] is None


def test_memory_status_active_memory_opt_in(client: TestClient) -> None:
    """``include_active_memory=true`` populates the v3.1 dashboard.

    The e2e fixture is legacy-only so the canonical scanners (insights,
    outcome_score) are absent — but the dashboard still renders with
    ``..._available=False`` rather than crashing the route."""
    r = client.get(
        "/memory/status",
        params={"workspace_id": "default", "include_active_memory": True},
    )
    assert r.status_code == 200, r.text
    am = r.json()["active_memory"]
    assert am is not None
    # Legacy-only fixture: insights table missing → proposals unavailable.
    assert am["proposals_available"] is False
    assert am["open_proposals"] == 0
    # Legacy-only: outcome_score column missing → predictive warnings unavailable.
    assert am["predictive_warnings_available"] is False
    assert am["predictive_warnings"] == 0
    # Persisted-warnings counter works on legacy memory_candidates table.
    assert am["persisted_warnings_new"] == 0
