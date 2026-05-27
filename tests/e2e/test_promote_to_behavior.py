"""End-to-end tests for v1.10 /memory/promote_candidate_to_behavior."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_factory) -> Iterator[TestClient]:
    app = app_factory(MEMORY_WORKSPACE_ID="ws-correction")
    with TestClient(app) as c:
        yield c


def _seed_correction_candidate(
    db_path: str,
    *,
    workspace_id: str = "ws-correction",
    candidate_id: str = "cand_correction_seed",
    subject: str = "Verify before claiming: filter audit by created_at > release date",
    confidence: float = 0.85,
) -> str:
    """Insert a CORRECTION-kind memory_candidate row directly.

    We bypass the extractor here and inject a row that looks exactly
    like one the CorrectionExtractor would have produced. Also seeds
    a placeholder episode so the candidate's source_episode_id
    foreign-key constraint is satisfied.
    """
    now = "2026-05-04T12:00:00+00:00"
    conn = sqlite3.connect(db_path)
    try:
        # Seed a placeholder source episode so the candidate's FK holds.
        conn.execute(
            """
            INSERT OR IGNORE INTO episodes (
                id, workspace_id, source_type, raw_text, trust_level,
                importance, confidence, created_at, metadata_json
            ) VALUES ('ep_seed', ?, 'user_message',
                      'placeholder seed for correction candidate test',
                      'user_asserted', 0.7, 1.0, ?, '{}')
            """,
            (workspace_id, now),
        )
        conn.execute(
            """
            INSERT INTO candidates (
                id, workspace_id, kind, subject, predicate, object, evidence,
                confidence, importance, trust_level, status,
                temporal_json, write_targets_json, metadata_json,
                source_episode_id, created_at, updated_at
            ) VALUES (?, ?, 'correction', ?, 'should_avoid', NULL,
                      'agent claim: x\nuser correction: y',
                      ?, ?, 'user_asserted', 'new',
                      ?, '["behavior_instruction","audit"]', '{}',
                      'ep_seed', ?, ?)
            """,
            (
                candidate_id,
                workspace_id,
                subject,
                confidence,
                confidence,
                f'{{"observed_at":"{now}","valid_from":"{now}"}}',
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return candidate_id


def _search_behaviors(client: TestClient, query: str):
    return client.post(
        "/memory/search",
        json={
            "workspace_id": "ws-correction",
            "kinds": ["behavior"],
            "query": query,
            "limit": 10,
        },
    )


def test_promote_correction_creates_behavior_instruction(client: TestClient, tmp_db_path) -> None:
    cand_id = _seed_correction_candidate(str(tmp_db_path))
    r = client.post(
        "/memory/promote_candidate_to_behavior",
        json={
            "workspace_id": "ws-correction",
            "candidate_id": cand_id,
            "name": "verify-timestamps-before-claiming",
            "rationale": "Lessons captured from audit-vs-release-date confusion.",
            "decided_by": "operator-test",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["candidate_id"] == cand_id
    assert body["status"] == "promoted"
    bi_id = body["behavior_instruction_id"]
    assert bi_id

    # Candidate should now be promoted with target_id set
    conn = sqlite3.connect(str(tmp_db_path))
    try:
        promoted = conn.execute(
            """
            SELECT status, promoted_target_type, promoted_target_id
            FROM candidates
            WHERE id = ?
            """,
            (cand_id,),
        ).fetchone()
    finally:
        conn.close()
    assert promoted is not None
    assert promoted[0] == "promoted"
    assert promoted[1] == "behavior_instruction"
    assert promoted[2] == bi_id

    # Behavior should be retrievable by canonical compact search.
    list_bi = _search_behaviors(client, "verify timestamps claiming")
    assert list_bi.status_code == 200, list_bi.text
    names = [bi["projection"]["name"] for bi in list_bi.json()["data"]]
    assert "verify-timestamps-before-claiming" in names


def test_promote_with_pinned_flag(client: TestClient, tmp_db_path) -> None:
    """v1.10 ``pinned`` parameter pins the resulting behavior_instruction."""
    cand_id = _seed_correction_candidate(str(tmp_db_path), candidate_id="cand_pinned_seed")
    r = client.post(
        "/memory/promote_candidate_to_behavior",
        json={
            "workspace_id": "ws-correction",
            "candidate_id": cand_id,
            "name": "pinned-rule",
            "pinned": True,
        },
    )
    assert r.status_code == 200, r.text
    list_bi = _search_behaviors(client, "pinned-rule")
    instructions = [item["projection"] for item in list_bi.json()["data"]]
    pinned_rule = next((bi for bi in instructions if bi["name"] == "pinned-rule"), None)
    assert pinned_rule is not None
    assert pinned_rule["pinned"] is True


def test_promote_with_rule_text_override(client: TestClient, tmp_db_path) -> None:
    cand_id = _seed_correction_candidate(str(tmp_db_path), candidate_id="cand_override_seed")
    r = client.post(
        "/memory/promote_candidate_to_behavior",
        json={
            "workspace_id": "ws-correction",
            "candidate_id": cand_id,
            "name": "rule-with-override",
            "rule_text_override": "Always check process start time before assuming staleness.",
            "decided_by": "operator-test",
        },
    )
    assert r.status_code == 200, r.text

    list_bi = _search_behaviors(client, "rule-with-override")
    rules = {}
    for item in list_bi.json()["data"]:
        bi = item["projection"]
        full = client.get(
            "/memory/get",
            params={
                "workspace_id": "ws-correction",
                "kind": "behavior",
                "id": bi["id"],
                "fields": "rule",
            },
        ).json()
        rules[bi["name"]] = full["data"]["rule"]
    assert "rule-with-override" in rules
    assert rules["rule-with-override"] == (
        "Always check process start time before assuming staleness."
    )


def test_strict_workspace_isolation_blocks_foreign_promote(app_factory, tmp_db_path) -> None:
    """With ``MEMORY_STRICT_WORKSPACE_ISOLATION=true`` the promote
    endpoint must reject calls whose body.workspace_id mismatches the
    process's ``MEMORY_WORKSPACE_ID``.

    Without this check, an operator running a project-mode chat could
    accidentally (or maliciously) promote a correction candidate into
    a foreign workspace's behavior_instructions.
    """
    app = app_factory(
        MEMORY_WORKSPACE_ID="ws-primary",
        MEMORY_STRICT_WORKSPACE_ISOLATION="true",
    )
    cand_id = _seed_correction_candidate(
        str(tmp_db_path),
        workspace_id="ws-secondary",
        candidate_id="cand_foreign_seed",
    )
    with TestClient(app) as foreign_client:
        r = foreign_client.post(
            "/memory/promote_candidate_to_behavior",
            json={
                "workspace_id": "ws-secondary",
                "candidate_id": cand_id,
                "name": "should-be-rejected",
            },
        )
    # ensure_workspace_writable raises ValidationError → 422
    assert r.status_code in (400, 403, 422), r.text


def test_name_collision_blocked_without_overwrite(client: TestClient, tmp_db_path) -> None:
    """SECURITY: a second promote with the same name returns 409
    unless overwrite=true is passed explicitly. Prevents silent
    rule replacement via UPSERT semantics."""
    cand_a = _seed_correction_candidate(str(tmp_db_path), candidate_id="cand_collision_a")
    cand_b = _seed_correction_candidate(str(tmp_db_path), candidate_id="cand_collision_b")
    r1 = client.post(
        "/memory/promote_candidate_to_behavior",
        json={
            "workspace_id": "ws-correction",
            "candidate_id": cand_a,
            "name": "shared-rule-name",
        },
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/memory/promote_candidate_to_behavior",
        json={
            "workspace_id": "ws-correction",
            "candidate_id": cand_b,
            "name": "shared-rule-name",
        },
    )
    assert r2.status_code == 409, r2.text
    # With explicit overwrite, the second promote succeeds.
    r3 = client.post(
        "/memory/promote_candidate_to_behavior",
        json={
            "workspace_id": "ws-correction",
            "candidate_id": cand_b,
            "name": "shared-rule-name",
            "overwrite": True,
        },
    )
    assert r3.status_code == 200, r3.text


def test_promote_unknown_candidate_404(client: TestClient) -> None:
    r = client.post(
        "/memory/promote_candidate_to_behavior",
        json={
            "workspace_id": "ws-correction",
            "candidate_id": "cand_does_not_exist",
            "name": "noop",
        },
    )
    assert r.status_code == 404


def test_promote_already_decided_returns_409(client: TestClient, tmp_db_path) -> None:
    cand_id = _seed_correction_candidate(str(tmp_db_path), candidate_id="cand_double_promote")
    r1 = client.post(
        "/memory/promote_candidate_to_behavior",
        json={
            "workspace_id": "ws-correction",
            "candidate_id": cand_id,
            "name": "first-promote",
        },
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/memory/promote_candidate_to_behavior",
        json={
            "workspace_id": "ws-correction",
            "candidate_id": cand_id,
            "name": "second-promote",
        },
    )
    assert r2.status_code == 409, r2.text


def test_promote_non_correction_candidate_returns_409(client: TestClient, tmp_db_path) -> None:
    """A DECISION-kind candidate must not be promotable through this endpoint."""
    conn = sqlite3.connect(str(tmp_db_path))
    try:
        now = "2026-05-04T12:00:00+00:00"
        conn.execute(
            """
            INSERT OR IGNORE INTO episodes (
                id, workspace_id, source_type, raw_text, trust_level,
                importance, confidence, created_at, metadata_json
            ) VALUES ('ep_seed', 'ws-correction', 'user_message',
                      'placeholder seed', 'user_asserted',
                      0.7, 1.0, ?, '{}')
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO candidates (
                id, workspace_id, kind, subject, predicate, object, evidence,
                confidence, importance, trust_level, status,
                temporal_json, write_targets_json, metadata_json,
                source_episode_id, created_at, updated_at
            ) VALUES ('cand_decision_seed', 'ws-correction', 'decision',
                      'switch to SQLite', 'declared', NULL,
                      'Decision: switch', 0.85, 0.80, 'user_asserted', 'new',
                      ?, '["decision","audit"]', '{}', 'ep_seed', ?, ?)
            """,
            (
                f'{{"observed_at":"{now}","valid_from":"{now}"}}',
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    r = client.post(
        "/memory/promote_candidate_to_behavior",
        json={
            "workspace_id": "ws-correction",
            "candidate_id": "cand_decision_seed",
            "name": "should-not-promote",
        },
    )
    assert r.status_code == 409, r.text
    assert "CORRECTION" in r.text or "correction" in r.text


def test_review_queue_lists_correction_candidate(client: TestClient, tmp_db_path) -> None:
    """Confirm the review queue exposes the correction candidate."""
    cand_id = _seed_correction_candidate(str(tmp_db_path), candidate_id="cand_review_envelope")
    listed = client.post(
        "/memory/review_queue",
        json={"workspace_id": "ws-correction", "limit_per_kind": 10},
    )
    assert listed.status_code == 200, listed.text
    text = str(listed.json())
    assert "correction" in text
    assert cand_id in text
