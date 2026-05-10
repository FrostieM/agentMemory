"""E2E for /memory/record_with_evidence (Move 2 of v2.2)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_compound_writes_episode_decision_and_link(client: TestClient) -> None:
    """The compound endpoint writes all three objects in one call:
    episode → decision (with source_episode_id threaded) → capability_link."""
    # Pre-create the skill the link will reference.
    skill = client.post(
        "/memory/upsert_agent_skill",
        json={
            "workspace_id": "default",
            "name": "Memory architecture",
            "summary": "Decisions about memory storage / retrieval shape",
            "when_to_use": ["picking storage backend"],
            "tools": ["memory_write_decision"],
        },
    )
    assert skill.status_code == 200, skill.text

    r = client.post(
        "/memory/record_with_evidence",
        json={
            "workspace_id": "default",
            "evidence_text": "benchmarked SQLite vs Postgres on 50k rows",
            "decision_title": "Use SQLite for memory store",
            "decision_text": "SQLite WAL is enough for our throughput.",
            "decision_rationale": "no Docker, footprint matters",
            "capability_type": "skill",
            "capability_name": "Memory architecture",
            "capability_relation": "owner",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["episode_id"].startswith("ep_")
    assert body["decision_id"].startswith("dec_")
    assert body["capability_link_id"] is not None
    assert body["capability_link_id"].startswith("caplink_")
    assert body["chunk_id"].startswith("chk_")
    assert body["decision_status"] == "active"

    # Verify provenance threading: the decision's source_episode_id
    # equals the just-created episode id.
    listed = client.post(
        "/memory/list_decisions",
        json={"workspace_id": "default", "query": "Use SQLite for memory", "limit": 1},
    ).json()
    assert listed["decisions"][0]["source_episode_id"] == body["episode_id"]


def test_compound_without_capability_skips_link(client: TestClient) -> None:
    """Omitting the capability triplet creates only episode + decision."""
    r = client.post(
        "/memory/record_with_evidence",
        json={
            "workspace_id": "default",
            "evidence_text": "decided to defer caching to v2",
            "decision_title": "No caching layer in v1",
            "decision_text": "Ship without an explicit cache; lru_cache where needed.",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["episode_id"].startswith("ep_")
    assert body["decision_id"].startswith("dec_")
    assert body["capability_link_id"] is None


def test_partial_capability_triplet_rejected(client: TestClient) -> None:
    """Pydantic schema validator: providing 1 of 3 capability fields fails."""
    r = client.post(
        "/memory/record_with_evidence",
        json={
            "workspace_id": "default",
            "evidence_text": "trying invalid input",
            "decision_title": "Test",
            "decision_text": "T",
            "capability_type": "skill",
            # missing capability_name and capability_relation → must reject
        },
    )
    assert r.status_code == 422, r.text


def test_compound_threads_source_episode_id_into_link(client: TestClient) -> None:
    """The capability link's source_episode_id matches the just-created
    episode — proves the bundle wires provenance into all three objects."""
    skill = client.post(
        "/memory/upsert_agent_skill",
        json={
            "workspace_id": "default",
            "name": "Auth refactor",
            "summary": "JWT migration know-how",
            "when_to_use": ["auth changes"],
            "tools": [],
        },
    )
    assert skill.status_code == 200, skill.text

    r = client.post(
        "/memory/record_with_evidence",
        json={
            "workspace_id": "default",
            "evidence_text": "auth security review identified session-cookie risks",
            "decision_title": "Move auth to JWT",
            "decision_text": "Replace session cookies with JWT for API endpoints.",
            "capability_type": "skill",
            "capability_name": "Auth refactor",
            "capability_relation": "method",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability_link_id"] is not None

    # Pull the link back through the listing endpoint and confirm
    # source_episode_id matches.
    links = client.post(
        "/memory/list_capability_links",
        json={
            "workspace_id": "default",
            "target_type": "decision",
            "target_id": body["decision_id"],
        },
    ).json()
    assert len(links["links"]) == 1
    assert links["links"][0]["source_episode_id"] == body["episode_id"]


def test_supersedes_chain_via_compound(client: TestClient) -> None:
    """Compound endpoint supports supersedes_decision_id like write_decision."""
    first = client.post(
        "/memory/record_with_evidence",
        json={
            "workspace_id": "default",
            "evidence_text": "v1 architecture initial draft",
            "decision_title": "v1",
            "decision_text": "first cut",
        },
    ).json()
    second = client.post(
        "/memory/record_with_evidence",
        json={
            "workspace_id": "default",
            "evidence_text": "v2 architecture revision",
            "decision_title": "v2",
            "decision_text": "revised cut",
            "supersedes_decision_id": first["decision_id"],
        },
    ).json()
    assert second["superseded_decision_id"] == first["decision_id"]
