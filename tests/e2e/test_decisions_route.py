from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_write_decision_returns_active(client: TestClient) -> None:
    response = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "Use SQLite + LanceDB",
            "decision_text": "Lite memory uses SQLite + LanceDB.",
            "rationale": "no Docker available",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert body["decision_id"].startswith("dec_")


def test_supersedes_chain_via_http(client: TestClient) -> None:
    first = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "v1",
            "decision_text": "first",
        },
    ).json()
    second = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "v2",
            "decision_text": "second",
            "supersedes_decision_id": first["decision_id"],
        },
    ).json()
    assert second["superseded_decision_id"] == first["decision_id"]


def test_unknown_supersedes_returns_404(client: TestClient) -> None:
    response = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "x",
            "decision_text": "y",
            "supersedes_decision_id": "dec_missing",
        },
    )
    assert response.status_code == 404


def test_list_decisions_searches_by_topic(client: TestClient) -> None:
    first = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "Live execution watchdog",
            "decision_text": "Keep live execution HTTP health cache-backed and non-blocking.",
            "rationale": "Operators need the topic-level decision without knowing its id.",
            "importance": 0.9,
        },
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "Frontend layout",
            "decision_text": "Use operator-first dashboards.",
            "importance": 0.9,
        },
    )
    assert second.status_code == 200, second.text

    response = client.post(
        "/memory/list_decisions",
        json={"workspace_id": "default", "query": "live execution health", "limit": 1},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["title"] for item in body["decisions"]] == ["Live execution watchdog"]
    assert body["decisions"][0]["decision_text"].endswith("non-blocking.")


def test_list_decisions_repairs_display_mojibake(client: TestClient) -> None:
    mojibake_dash = "\u00e2\u0080\u0094"
    response = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": f"API process {mojibake_dash} worker",
            "decision_text": f"Split API {mojibake_dash} worker for reliability.",
            "importance": 0.9,
        },
    )
    assert response.status_code == 200, response.text

    listed = client.post(
        "/memory/list_decisions",
        json={"workspace_id": "default", "query": "API worker", "limit": 1},
    )

    assert listed.status_code == 200, listed.text
    item = listed.json()["decisions"][0]
    assert item["title"] == "API process \u2014 worker"
    assert item["decision_text"] == "Split API \u2014 worker for reliability."


# ---------- 2.2 Move 1: auto-thread source_episode_id ----------


def test_auto_thread_fills_from_recent_ingest_episode(client: TestClient) -> None:
    """When the same agent_id has just ingested an episode, the next
    write_decision without source_episode_id picks it up automatically."""
    headers = {"X-Memory-Agent-Id": "claude-test"}
    ep = client.post(
        "/memory/ingest_episode",
        headers=headers,
        json={
            "workspace_id": "default",
            "raw_text": "exploring the auth refactor",
            "source_type": "agent_action",
            "trust_level": "agent_observed",
            "importance": 0.6,
        },
    )
    assert ep.status_code == 200, ep.text
    episode_id = ep.json()["episode_id"]
    dec = client.post(
        "/memory/write_decision",
        headers=headers,
        json={
            "workspace_id": "default",
            "title": "Move auth to JWT",
            "decision_text": "Replace session cookies with JWT.",
        },
    )
    assert dec.status_code == 200, dec.text
    listed = client.post(
        "/memory/list_decisions",
        json={"workspace_id": "default", "query": "Move auth to JWT", "limit": 1},
    ).json()
    assert listed["decisions"][0]["source_episode_id"] == episode_id


def test_allow_orphan_skips_auto_thread(client: TestClient) -> None:
    """allow_orphan=True keeps source_episode_id NULL even when a
    matching recent episode exists."""
    headers = {"X-Memory-Agent-Id": "claude-test"}
    client.post(
        "/memory/ingest_episode",
        headers=headers,
        json={
            "workspace_id": "default",
            "raw_text": "irrelevant context",
            "source_type": "agent_action",
        },
    )
    dec = client.post(
        "/memory/write_decision",
        headers=headers,
        json={
            "workspace_id": "default",
            "title": "Pre-existing decision",
            "decision_text": "Decision predates the recording of any episode.",
            "allow_orphan": True,
        },
    )
    assert dec.status_code == 200, dec.text
    listed = client.post(
        "/memory/list_decisions",
        json={"workspace_id": "default", "query": "Pre-existing decision", "limit": 1},
    ).json()
    assert listed["decisions"][0]["source_episode_id"] is None


def test_explicit_source_episode_id_wins_over_auto_thread(client: TestClient) -> None:
    """When the caller passes source_episode_id explicitly, the
    server uses that value verbatim \u2014 no override by the auto-thread
    helper, even if a more recent episode exists for this agent."""
    headers = {"X-Memory-Agent-Id": "claude-test"}
    older = client.post(
        "/memory/ingest_episode",
        headers=headers,
        json={
            "workspace_id": "default",
            "raw_text": "older episode",
            "source_type": "agent_action",
        },
    ).json()["episode_id"]
    client.post(
        "/memory/ingest_episode",
        headers=headers,
        json={
            "workspace_id": "default",
            "raw_text": "newer episode",
            "source_type": "agent_action",
        },
    )
    dec = client.post(
        "/memory/write_decision",
        headers=headers,
        json={
            "workspace_id": "default",
            "title": "Explicit source decision",
            "decision_text": "T",
            "source_episode_id": older,
        },
    )
    assert dec.status_code == 200, dec.text
    listed = client.post(
        "/memory/list_decisions",
        json={"workspace_id": "default", "query": "Explicit source decision", "limit": 1},
    ).json()
    assert listed["decisions"][0]["source_episode_id"] == older
