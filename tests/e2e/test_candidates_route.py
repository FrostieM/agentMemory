from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="project-a")
    with TestClient(app) as c:
        yield c


def test_ingest_writes_reviewable_candidate(client: TestClient) -> None:
    ingest = client.post(
        "/memory/ingest_episode",
        json={
            "workspace_id": "project-a",
            "source_type": "agent_action",
            "raw_text": "Decision: keep extraction reviewable",
            "trust_level": "verified_by_tool",
        },
    )
    assert ingest.status_code == 200, ingest.text
    assert ingest.json()["candidates_written"] == 1
    assert ingest.json()["auto_promoted_decisions"] == 0

    listed = client.post("/memory/list_candidates", json={"workspace_id": "project-a"})
    assert listed.status_code == 200, listed.text
    candidates = listed.json()["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["status"] == "new"

    promoted = client.post(
        "/memory/promote_candidate",
        json={"candidate_id": candidates[0]["candidate_id"]},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "promoted"
    assert promoted.json()["promoted_target_type"] == "decision"
