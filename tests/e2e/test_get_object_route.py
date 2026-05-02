"""End-to-end tests for the discover-then-fetch endpoint.

Covers:
  * /memory/get_object correctly fetches each supported kind by id.
  * `<index>` blocks appear in the get_context envelope when the long
    tail of relevant items exceeds the full-render cap.
  * memory_get_object delivers the body the agent saw as a `<ref/>`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory()
    with TestClient(app) as c:
        yield c


def test_get_object_fetches_decision_by_id(client: TestClient) -> None:
    write = client.post(
        "/memory/write_decision",
        json={
            "workspace_id": "default",
            "title": "Discover-then-fetch primer",
            "decision_text": "Render top-N full + <index> for the long tail.",
            "rationale": "Stops the hard cap from silently dropping context.",
        },
    )
    assert write.status_code == 200
    decision_id = write.json()["decision_id"]

    fetched = client.post(
        "/memory/get_object",
        json={"workspace_id": "default", "kind": "decision", "id": decision_id},
    )
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["found"] is True
    assert body["kind"] == "decision"
    assert body["id"] == decision_id
    obj = body["object"]
    assert obj is not None
    assert obj["title"] == "Discover-then-fetch primer"
    assert "Render top-N" in obj["decision_text"]


def test_get_object_returns_not_found_for_unknown_id(client: TestClient) -> None:
    response = client.post(
        "/memory/get_object",
        json={"workspace_id": "default", "kind": "decision", "id": "dec_does_not_exist"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is False
    assert body["object"] is None


def test_get_object_rejects_unknown_kind(client: TestClient) -> None:
    response = client.post(
        "/memory/get_object",
        json={"workspace_id": "default", "kind": "not_a_kind", "id": "x"},
    )
    assert response.status_code == 422  # pydantic Literal validation


def test_get_object_resolves_role_and_skill_and_playbook(client: TestClient) -> None:
    role = client.post(
        "/memory/upsert_agent_role",
        json={
            "workspace_id": "default",
            "name": "Test role",
            "purpose": "Role for fetch test",
        },
    )
    skill = client.post(
        "/memory/upsert_agent_skill",
        json={
            "workspace_id": "default",
            "name": "Test skill",
            "summary": "Skill for fetch test",
        },
    )
    playbook = client.post(
        "/memory/upsert_agent_playbook",
        json={
            "workspace_id": "default",
            "name": "Test playbook",
            "goal": "Playbook for fetch test",
        },
    )
    assert role.status_code == 200
    assert skill.status_code == 200
    assert playbook.status_code == 200
    rid = role.json()["role_id"]
    sid = skill.json()["skill_id"]
    pid = playbook.json()["playbook_id"]

    for kind, oid, name in (
        ("role", rid, "Test role"),
        ("skill", sid, "Test skill"),
        ("playbook", pid, "Test playbook"),
    ):
        r = client.post(
            "/memory/get_object",
            json={"workspace_id": "default", "kind": kind, "id": oid},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is True, f"{kind} {oid} not found"
        assert body["object"]["name"] == name


def test_get_context_renders_index_block_for_long_tail(client: TestClient) -> None:
    # Create more decisions than fit in the full-render cap (MAX_DECISIONS=10).
    for i in range(14):
        r = client.post(
            "/memory/write_decision",
            json={
                "workspace_id": "default",
                "title": f"Index test decision {i:02d}",
                "decision_text": f"body {i}",
                "rationale": "fixture",
                "importance": 0.5,
            },
        )
        assert r.status_code == 200, r.text

    response = client.post(
        "/memory/get_context",
        json={"workspace_id": "default", "query": "index test decision", "max_tokens": 6000},
    )
    assert response.status_code == 200
    text = response.json()["context_text"]
    # The long-tail index should appear inside <active_decisions>.
    assert "<index" in text
    assert "<ref id=" in text
    # At least one ref should reference one of our test decisions.
    assert "Index test decision" in text


def test_get_object_includes_theory_evidence_on_request(client: TestClient) -> None:
    write_theory = client.post(
        "/memory/write_theory",
        json={
            "workspace_id": "default",
            "title": "Test theory for evidence include",
            "domain": "test",
            "claim": "Test claim",
            "predictions": ["pred"],
            "validation_criteria": ["criterion"],
        },
    )
    assert write_theory.status_code == 200
    theory_id = write_theory.json()["theory_id"]
    add_evidence = client.post(
        "/memory/add_theory_evidence",
        json={
            "workspace_id": "default",
            "theory_id": theory_id,
            "kind": "supporting",
            "summary": "Supporting evidence for the test theory",
            "confidence": 0.8,
        },
    )
    assert add_evidence.status_code == 200

    without_ev = client.post(
        "/memory/get_object",
        json={"workspace_id": "default", "kind": "theory", "id": theory_id},
    )
    assert without_ev.status_code == 200
    assert without_ev.json()["object"]["evidence"] == []

    with_ev = client.post(
        "/memory/get_object",
        json={
            "workspace_id": "default",
            "kind": "theory",
            "id": theory_id,
            "include_evidence": True,
        },
    )
    assert with_ev.status_code == 200
    evidence_list = with_ev.json()["object"]["evidence"]
    assert len(evidence_list) >= 1
    assert evidence_list[0]["summary"].startswith("Supporting evidence")


def test_get_context_index_block_appears_for_research_agenda(client: TestClient) -> None:
    """Long-tail experiments / insights / concepts must surface as <ref/>."""
    # Seed enough concepts to overflow MAX_RESEARCH_AGENDA = 5.
    for i in range(8):
        r = client.post(
            "/memory/upsert_concept",
            json={
                "workspace_id": "default",
                "name": f"index-test-concept-{i:02d}",
                "kind": "term",
                "definition": "Concept seeded to verify research_agenda index rendering.",
            },
        )
        assert r.status_code == 200, r.text

    response = client.post(
        "/memory/get_context",
        json={"workspace_id": "default", "query": "index-test-concept", "max_tokens": 4000},
    )
    assert response.status_code == 200
    text = response.json()["context_text"]
    # <research_agenda> must contain at least one <index ...> block now.
    assert "<research_agenda>" in text
    inside = text.split("<research_agenda>", 1)[1].split("</research_agenda>", 1)[0]
    assert "<index" in inside, "no index block inside <research_agenda>"
    assert "<ref id=" in inside


def test_get_object_renders_full_body_after_index_discovery(client: TestClient) -> None:
    """Round-trip: a ref id surfaced in the index can be expanded by get_object."""
    # Create more decisions than the full-render cap so the index will list refs.
    seed_ids: list[str] = []
    for i in range(14):
        r = client.post(
            "/memory/write_decision",
            json={
                "workspace_id": "default",
                "title": f"Round-trip decision {i:02d}",
                "decision_text": f"detail body for round-trip decision {i}",
                "importance": 0.5,
            },
        )
        assert r.status_code == 200, r.text
        seed_ids.append(r.json()["decision_id"])

    ctx = client.post(
        "/memory/get_context",
        json={"workspace_id": "default", "query": "round-trip decision", "max_tokens": 4000},
    )
    text = ctx.json()["context_text"]
    # Pick one of our seeded decisions and confirm we can fetch its full body
    # via the discover-then-fetch path.
    target = seed_ids[-1]
    fetch = client.post(
        "/memory/get_object",
        json={"workspace_id": "default", "kind": "decision", "id": target},
    )
    assert fetch.status_code == 200
    body = fetch.json()
    assert body["found"] is True
    assert body["object"]["title"].startswith("Round-trip decision")
    # Sanity: the index block in the envelope referenced this id (or an earlier
    # peer; with 14 seeds and full-cap 10 there's at least one ref).
    assert "<ref id=" in text
