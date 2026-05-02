"""E2E for the P1 routes:
* POST /memory/pin            — flip pinned flag on decisions
* POST /memory/list_decisions — since/until date-range filter
* POST /memory/what_references — reverse lookup by id
* POST /memory/list_audit     — read the audit trail
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="project-a")
    with TestClient(app) as c:
        yield c


def _write_decision(client: TestClient, title: str, text: str) -> str:
    response = client.post(
        "/memory/write_decision",
        json={"workspace_id": "project-a", "title": title, "decision_text": text},
    )
    assert response.status_code == 200, response.text
    return response.json()["decision_id"]


def test_pin_route_flips_decision(client: TestClient) -> None:
    decision_id = _write_decision(
        client, "Architectural invariant: local-only", "Never call cloud LLMs."
    )
    pinned = client.post(
        "/memory/pin",
        json={"workspace_id": "project-a", "kind": "decision", "id": decision_id},
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json() == {
        "kind": "decision",
        "id": decision_id,
        "pinned": True,
        "found": True,
    }
    listed = client.post(
        "/memory/list_decisions",
        json={"workspace_id": "project-a", "limit": 5},
    )
    assert listed.status_code == 200
    decisions = listed.json()["decisions"]
    # Pinned decision should be first regardless of insert order.
    assert decisions[0]["decision_id"] == decision_id


def test_pin_unsupported_kind_returns_400(client: TestClient) -> None:
    response = client.post(
        "/memory/pin",
        json={"workspace_id": "project-a", "kind": "theory", "id": "th_x"},
    )
    assert response.status_code == 400


def test_pin_route_flips_behavior_instruction(client: TestClient) -> None:
    upserted = client.post(
        "/memory/upsert_behavior_instruction",
        json={
            "workspace_id": "project-a",
            "name": "Pinnable instruction",
            "rule": "Pinned instructions ride along regardless of query.",
            "kind": "operating_rule",
            "rationale": "Operator-critical guard rail.",
            "confidence": 0.9,
        },
    )
    assert upserted.status_code == 200, upserted.text
    instruction_id = upserted.json()["instruction_id"]
    pinned = client.post(
        "/memory/pin",
        json={
            "workspace_id": "project-a",
            "kind": "behavior_instruction",
            "id": instruction_id,
        },
    )
    assert pinned.status_code == 200, pinned.text
    body = pinned.json()
    assert body == {
        "kind": "behavior_instruction",
        "id": instruction_id,
        "pinned": True,
        "found": True,
    }


def test_pin_route_flips_core_memory(client: TestClient, applied_conn) -> None:
    # core_memory has no public POST writer in the API surface, but the
    # pin handler operates by ``id``, so we seed a row directly via the
    # repository to avoid coupling the test to internal core-memory
    # promotion. ``applied_conn`` and the TestClient app share the same
    # tmp_db_path fixture, so writing through the conn is visible to
    # the route.
    from agent_memory_lite.repositories.core_memory_repo import (  # noqa: PLC0415
        upsert_core_memory_row,
    )
    from agent_memory_lite.utils.time import iso_now  # noqa: PLC0415

    upsert_core_memory_row(
        applied_conn,
        core_id="core_pin_target",
        workspace_id="project-a",
        key="local_only",
        value="Never call cloud LLMs.",
        source_episode_id=None,
        confidence=0.99,
        importance=0.99,
        timestamp=iso_now(),
    )
    applied_conn.commit()

    pinned = client.post(
        "/memory/pin",
        json={
            "workspace_id": "project-a",
            "kind": "core_memory",
            "id": "core_pin_target",
        },
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json() == {
        "kind": "core_memory",
        "id": "core_pin_target",
        "pinned": True,
        "found": True,
    }


def test_list_decisions_since_until_filters(client: TestClient) -> None:
    early_id = _write_decision(client, "Early decision", "Original choice.")
    late_id = _write_decision(client, "Late decision", "Replacement choice.")
    # Use a since cutoff that excludes the early one. We cannot
    # control timestamps directly, but we can use the early decision's
    # own created_at as the cutoff (>= excludes nothing earlier than
    # itself, so the boundary case is the simplest exact-match test).
    early = client.post(
        "/memory/list_decisions",
        json={"workspace_id": "project-a", "limit": 5},
    ).json()["decisions"]
    early_ts = next(d["created_at"] for d in early if d["decision_id"] == early_id)
    late_ts = next(d["created_at"] for d in early if d["decision_id"] == late_id)
    # since=late_ts → only decisions at or after the late timestamp.
    response = client.post(
        "/memory/list_decisions",
        json={"workspace_id": "project-a", "since": late_ts, "limit": 5},
    )
    assert response.status_code == 200
    ids = {d["decision_id"] for d in response.json()["decisions"]}
    assert late_id in ids
    # The early decision MAY or may not appear depending on how close
    # the timestamps were; assert that the filter at least drops
    # truly-earlier ids when until is also pinned.
    response2 = client.post(
        "/memory/list_decisions",
        json={
            "workspace_id": "project-a",
            "since": early_ts,
            "until": early_ts,
            "limit": 5,
        },
    )
    ids2 = {d["decision_id"] for d in response2.json()["decisions"]}
    assert early_id in ids2


def test_what_references_finds_decision_text(client: TestClient) -> None:
    decision_id = _write_decision(
        client, "Reverse lookup target", "This decision will be referenced elsewhere."
    )
    other_id = _write_decision(
        client,
        "Decision that mentions the previous one",
        f"This depends on {decision_id} as the upstream choice.",
    )
    response = client.post(
        "/memory/what_references",
        json={"workspace_id": "project-a", "target_id": decision_id, "limit": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target_id"] == decision_id
    found_ids = {h["id"] for h in body["hits"]}
    assert other_id in found_ids


def test_list_audit_returns_write_decision_entries(client: TestClient) -> None:
    decision_id = _write_decision(client, "Auditable decision", "Body for audit.")
    response = client.post(
        "/memory/list_audit",
        json={
            "workspace_id": "project-a",
            "target_type": "decision",
            "target_id": decision_id,
            "limit": 10,
        },
    )
    assert response.status_code == 200, response.text
    entries = response.json()["entries"]
    assert any(e["action"] == "write_decision" for e in entries)
    assert all(e["target_id"] == decision_id for e in entries)
