"""v1.10 end-to-end correction loop test.

Verifies the full pipeline: ingest two episodes (claim + correction with
cross-reference metadata), confirm the CorrectionExtractor surfaced a
memory_candidate(kind=CORRECTION), promote it through the new endpoint,
and check the resulting behavior_instruction reaches the next
``/memory/get_context`` envelope.

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
    with TestClient(app) as c:
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
        "/memory/list_candidates",
        json={"workspace_id": "ws-loop", "statuses": ["new"], "limit": 20},
    )
    assert listed.status_code == 200, listed.text
    candidates = [c for c in listed.json()["candidates"] if c["kind"] == "correction"]
    assert candidates, "CorrectionExtractor did not produce a CORRECTION candidate"
    cand = candidates[0]
    assert cand["confidence"] >= 0.5
    assert "Verify before claiming" in cand["subject"]
    # subject is the distilled rule from the correction's first sentence;
    # for this fixture it should reference MCP / 1.1.0 / release dates
    subject_lower = cand["subject"].lower()
    assert any(token in subject_lower for token in ("mcp", "1.1.0", "audit", "релиз"))

    # 4. <pending_review> envelope surfaces the correction queue.
    ctx = client.post(
        "/memory/get_context",
        json={"workspace_id": "ws-loop", "query": "anything", "max_tokens": 2000},
    )
    assert ctx.status_code == 200, ctx.text
    text = ctx.json()["context_text"]
    assert "<pending_review" in text
    assert 'kind="correction_candidate"' in text
    assert "promote_candidate_to_behavior" in text

    # 5. Promote with operator-supplied name + decision_by.
    promoted = client.post(
        "/memory/promote_candidate_to_behavior",
        json={
            "workspace_id": "ws-loop",
            "candidate_id": cand["candidate_id"],
            "name": "filter-audit-by-release-date",
            "rule_text_override": (
                "When measuring whether a feature is dormant, filter "
                "audit_log by created_at > release_date before counting."
            ),
            "decided_by": "operator-loop-test",
        },
    )
    assert promoted.status_code == 200, promoted.text
    bi_id = promoted.json()["behavior_instruction_id"]
    assert bi_id

    # 6. New context envelope surfaces the behavior_instruction.
    ctx2 = client.post(
        "/memory/get_context",
        json={
            "workspace_id": "ws-loop",
            "query": "should I trust this measurement",
            "max_tokens": 2000,
        },
    )
    assert ctx2.status_code == 200, ctx2.text
    text2 = ctx2.json()["context_text"]
    assert "behavior_instructions" in text2
    assert "filter-audit-by-release-date" in text2 or "release_date" in text2

    # 7. Candidate status flipped to promoted with target lineage.
    promoted_listed = client.post(
        "/memory/list_candidates",
        json={"workspace_id": "ws-loop", "statuses": ["promoted"], "limit": 5},
    )
    promoted_rows = [
        c for c in promoted_listed.json()["candidates"] if c["candidate_id"] == cand["candidate_id"]
    ]
    assert promoted_rows
    assert promoted_rows[0]["promoted_target_type"] == "behavior_instruction"
    assert promoted_rows[0]["promoted_target_id"] == bi_id


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
        "/memory/list_candidates",
        json={"workspace_id": "ws-loop", "statuses": ["new"], "limit": 50},
    )
    corrections = [c for c in listed.json()["candidates"] if c["kind"] == "correction"]
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
    with TestClient(app) as client:
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
            "/memory/list_candidates",
            json={"workspace_id": "ws-disabled", "statuses": ["new"], "limit": 20},
        )
        assert listed.status_code == 200, listed.text
        corrections = [c for c in listed.json()["candidates"] if c["kind"] == "correction"]
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
