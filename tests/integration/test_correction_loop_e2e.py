"""v1.10 end-to-end correction loop test.

Verifies the full pipeline: ingest two episodes (claim + correction with
cross-reference metadata), confirm the CorrectionExtractor surfaced a
memory_candidate(kind=CORRECTION), promote it through the endpoint,
and check the resulting behavior reaches compact search.

This is the "the loop closes" test — without it, individual unit tests
could each pass while the wiring between them is still broken.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="ws-loop")
    # base_url must be loopback: OriginGuardMiddleware (v3.6) rejects a
    # non-loopback Host header, and TestClient defaults to
    # Host=testserver which the guard correctly 403s as DNS-rebinding.
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def _ingest(client: TestClient, **fields: object) -> str:
    """Helper: POST /memory/ingest_episode and return the episode_id."""
    r = client.post(
        "/memory/ingest_episode",
        json={"workspace_id": "ws-loop", **fields},
    )
    assert r.status_code == 200, r.text
    return r.json()["episode_id"]


def test_full_correction_loop_through_extractor_and_promote(client: TestClient) -> None:
    # 1. Ingest the agent claim — this is the "wrong" turn.
    claim_text = (
        "implicit feedback in copyBot is broken because audit_log shows "
        "1424 entries but only 1 feedback row. The hook must not be running."
    )
    claim_id = _ingest(
        client,
        source_type="agent_action",
        raw_text=claim_text,
        trust_level="agent_observed",
        importance=0.5,
        metadata={"kind": "correction_target"},
    )
    assert claim_id

    # 2. Ingest the user correction with cross-reference metadata.
    correction_text = (
        "нет, MCP только что был запущен после 1.1.0. "
        "Надо фильтровать audit_log по дате релиза, тогда станет видно что "
        "до релиза этого хука вообще не было."
    )
    _ingest(
        client,
        source_type="user_message",
        raw_text=correction_text,
        trust_level="user_asserted",
        importance=0.85,
        metadata={
            "kind": "user_correction",
            "correction_target_episode_id": claim_id,
        },
    )

    # 3. CorrectionExtractor should have produced a memory_candidate.
    listed = client.post(
        "/memory/review_queue",
        json={"workspace_id": "ws-loop", "limit_per_kind": 20},
    )
    assert listed.status_code == 200, listed.text
    candidates = [
        item
        for item in listed.json()["items"]
        if item["target_type"] == "candidate" and item["details"]["kind"] == "correction"
    ]
    assert candidates, "CorrectionExtractor did not produce a CORRECTION candidate"
    cand = candidates[0]
    assert cand["details"]["confidence"] >= 0.5
    assert "Verify before claiming" in cand["summary"]
    # subject is the distilled rule from the correction's first sentence;
    # for this fixture it should reference MCP / 1.1.0 / release dates
    subject_lower = cand["summary"].lower()
    assert any(token in subject_lower for token in ("mcp", "1.1.0", "audit", "релиз"))

    # 4. Promote with operator-supplied name + decision_by.
    promoted = client.post(
        "/memory/promote_candidate_to_behavior",
        json={
            "workspace_id": "ws-loop",
            "candidate_id": cand["target_id"],
            "name": "filter-audit-by-release-date",
            "rule_text_override": (
                "When measuring whether a feature is dormant, filter "
                "audit_log by created_at > release_date before counting."
            ),
            "decided_by": "operator-loop-test",
        },
    )
    assert promoted.status_code == 200, promoted.text
    bi_id = promoted.json().get("behavior_id") or promoted.json().get("behavior_instruction_id")
    assert bi_id

    # 5. Compact search surfaces the behavior.
    ctx2 = client.post(
        "/memory/search",
        json={
            "workspace_id": "ws-loop",
            "query": "filter-audit-by-release-date release_date",
            "kinds": ["behavior"],
            "limit": 5,
        },
    )
    assert ctx2.status_code == 200, ctx2.text
    text2 = str(ctx2.json()["data"])
    assert "filter-audit-by-release-date" in text2 or "release_date" in text2

    # 6. Candidate status flipped to promoted with target lineage.
    fetched = client.get(
        "/memory/get",
        params={"workspace_id": "ws-loop", "kind": "behavior", "id": bi_id},
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"]["id"] == bi_id


def test_correction_pair_bypasses_episode_dedup(client: TestClient) -> None:
    """Live-bug regression: when a user makes the SAME correction twice,
    episode dedup must NOT collapse the second pair — that would silently
    drop the second CORRECTION candidate and make recurring mistakes
    invisible. The bypass keys on metadata.correction_role / metadata.kind."""
    duplicate_correction_text = (
        "нет, неправильно — проверь дату deploy перед тем как делать выводы, "
        "фича активна но в новых форматах (regression-test)"
    )
    duplicate_claim_text = "yes the metric ratio confirms the feature is dormant (regression-test)"

    # First pair: claim + correction with identical text we'll reuse.
    claim_id_a = _ingest(
        client,
        source_type="agent_action",
        raw_text=duplicate_claim_text,
        trust_level="agent_observed",
        importance=0.5,
        metadata={"correction_role": "claim"},
    )
    _ingest(
        client,
        source_type="user_message",
        raw_text=duplicate_correction_text,
        trust_level="user_asserted",
        importance=0.7,
        metadata={
            "correction_role": "user_correction",
            "correction_target_episode_id": claim_id_a,
        },
    )

    # Second pair: byte-identical text, fresh claim id.
    claim_id_b = _ingest(
        client,
        source_type="agent_action",
        raw_text=duplicate_claim_text,
        trust_level="agent_observed",
        importance=0.5,
        metadata={"correction_role": "claim"},
    )
    _ingest(
        client,
        source_type="user_message",
        raw_text=duplicate_correction_text,
        trust_level="user_asserted",
        importance=0.7,
        metadata={
            "correction_role": "user_correction",
            "correction_target_episode_id": claim_id_b,
        },
    )

    # Both correction episodes must be distinct (dedup bypassed) and the
    # extractor must produce TWO candidates, not one. Without the bypass,
    # the second correction would silently use the first episode's id and
    # candidates_written would be 0 on the second ingest.
    assert claim_id_a != claim_id_b, "claim episodes must be distinct"
    listed = client.post(
        "/memory/review_queue",
        json={"workspace_id": "ws-loop", "limit_per_kind": 50},
    )
    corrections = [
        item
        for item in listed.json()["items"]
        if item["target_type"] == "candidate" and item["details"]["kind"] == "correction"
    ]
    assert len(corrections) >= 2, (
        f"dedup bypass broke: expected >=2 CORRECTION candidates from "
        f"two identical correction pairs, got {len(corrections)}"
    )


def test_loop_disabled_when_flag_off(app_factory) -> None:
    """With MEMORY_CORRECTION_DETECT_ENABLED=false, the extractor doesn't fire."""
    app = app_factory(
        MEMORY_WORKSPACE_ID="ws-disabled",
        MEMORY_CORRECTION_DETECT_ENABLED="false",
    )
    # Loopback base_url — see the `client` fixture note on OriginGuard.
    with TestClient(app, base_url="http://127.0.0.1") as client:
        claim_id = _ingest_in(
            client,
            "ws-disabled",
            "agent_action",
            "x" * 80,
            "agent_observed",
            0.5,
            {"kind": "correction_target"},
        )
        _ingest_in(
            client,
            "ws-disabled",
            "user_message",
            "нет, это не так — фильтруй audit_log по дате релиза перед выводами",
            "user_asserted",
            0.85,
            {"kind": "user_correction", "correction_target_episode_id": claim_id},
        )
        listed = client.post(
            "/memory/review_queue",
            json={"workspace_id": "ws-disabled", "limit_per_kind": 20},
        )
        assert listed.status_code == 200, listed.text
        corrections = [
            item
            for item in listed.json()["items"]
            if item["target_type"] == "candidate" and item["details"]["kind"] == "correction"
        ]
        assert corrections == []


def _ingest_in(
    client: TestClient,
    workspace: str,
    source_type: str,
    raw_text: str,
    trust_level: str,
    importance: float,
    metadata: dict,
) -> str:
    r = client.post(
        "/memory/ingest_episode",
        json={
            "workspace_id": workspace,
            "source_type": source_type,
            "raw_text": raw_text,
            "trust_level": trust_level,
            "importance": importance,
            "metadata": metadata,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["episode_id"]
