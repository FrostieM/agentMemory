from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agent_memory_lite.api.ui_telemetry import ui_telemetry


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_capability_routes_feed_context(client: TestClient) -> None:
    role = client.post(
        "/memory/upsert_agent_role",
        json={
            "workspace_id": "default",
            "name": "Runtime operator",
            "purpose": "Validate live system health before recovery.",
            "responsibilities": ["Check health endpoints", "Preserve evidence"],
            "boundaries": ["Do not reset data without explicit approval"],
            "tools": ["/memory/get_context", "/health"],
            "confidence": 0.85,
        },
    )
    assert role.status_code == 200, role.text
    assert role.json()["role_id"].startswith("role_")

    skill = client.post(
        "/memory/upsert_agent_skill",
        json={
            "workspace_id": "default",
            "name": "Live flow audit",
            "summary": "Validate runtime readiness, pipeline health, and business-flow blockers.",
            "when_to_use": ["The user asks whether the live system works"],
            "inputs": ["Health JSON", "Pipeline JSON"],
            "outputs": ["Exact blocker evidence"],
            "related_roles": ["Runtime operator"],
            "confidence": 0.9,
        },
    )
    assert skill.status_code == 200, skill.text
    assert skill.json()["skill_id"].startswith("skill_")

    playbook = client.post(
        "/memory/upsert_agent_playbook",
        json={
            "workspace_id": "default",
            "name": "Non-destructive live audit",
            "goal": "Confirm live flow without resetting data.",
            "triggers": ["The user asks for a health check"],
            "steps": ["Read memory context", "Check endpoints", "Report blockers"],
            "success_criteria": ["Context includes relevant capability guidance"],
            "required_skills": ["Live flow audit"],
            "confidence": 0.88,
        },
    )
    assert playbook.status_code == 200, playbook.text
    assert playbook.json()["playbook_id"].startswith("play_")

    capabilities = client.post(
        "/memory/list_agent_capabilities",
        json={"workspace_id": "default", "query": "live flow health", "limit": 6},
    )
    assert capabilities.status_code == 200, capabilities.text
    body = capabilities.json()
    assert body["roles"][0]["name"] == "Runtime operator"
    assert body["skills"][0]["name"] == "Live flow audit"
    assert body["playbooks"][0]["name"] == "Non-destructive live audit"

    ui_telemetry.clear()
    context = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "default",
            "query": "live flow health audit",
            "max_tokens": 2500,
        },
    )
    assert context.status_code == 200, context.text
    text = context.json()["context_text"]
    assert "<agent_capabilities>" in text
    assert "Runtime operator" in text
    assert "Live flow audit" in text
    assert "Non-destructive live audit" in text

    state = client.get("/memory/ui/state?workspace_id=default")
    assert state.status_code == 200, state.text
    used_events = [
        event
        for event in state.json()["latest_events"]
        if event["type"] == "graph_delta" and event["counts"].get("action") == "used"
    ]
    used_object_types = {event["counts"].get("object_type") for event in used_events}
    assert {"role", "skill", "playbook"}.issubset(used_object_types)
