from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_skill_write_feeds_compact_search(client: TestClient) -> None:
    skill = client.post(
        "/memory/write",
        json={
            "workspace_id": "default",
            "kind": "skill",
            "payload": {
                "name": "Live flow audit",
                "summary": "Validate runtime readiness, pipeline health, and business-flow blockers.",
                "body_md": "Validate runtime readiness, pipeline health, and business-flow blockers.",
                "trigger": "The user asks whether the live system works.",
                "active": True,
                "confidence": 0.9,
            },
        },
    )
    assert skill.status_code == 200, skill.text
    assert skill.json()["data"]["id"].startswith("skill_")

    found = client.post(
        "/memory/search",
        json={
            "workspace_id": "default",
            "query": "live flow health audit",
            "kinds": ["skill"],
            "limit": 6,
        },
    )
    assert found.status_code == 200, found.text
    assert "Live flow audit" in str(found.json()["data"])
