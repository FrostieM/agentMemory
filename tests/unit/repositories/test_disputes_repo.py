"""v3.3 Vector 6 — disputes_repo CRUD tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.repositories.disputes_repo import (
    Dispute,
    get_dispute,
    insert_dispute,
    list_disputes,
    resolve_dispute,
)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def test_insert_and_read_round_trip(conn: sqlite3.Connection) -> None:
    dispute_id = insert_dispute(
        conn,
        workspace_id="ws",
        target_kind="decision",
        target_id="dec_alpha",
        claimant_agent_id="agent_b",
        claim_text="dec_alpha contradicts the post-mortem from 2026-05-01",
        evidence=[{"kind": "episode", "id": "ep_xx"}],
    )
    assert dispute_id.startswith("audit_") or dispute_id  # any non-empty id
    d = get_dispute(conn, dispute_id=dispute_id)
    assert d is not None
    assert isinstance(d, Dispute)
    assert d.status == "open"
    assert d.target_id == "dec_alpha"
    assert d.evidence == [{"kind": "episode", "id": "ep_xx"}]
    assert d.resolution == ""
    assert d.resolved_at == ""


def test_resolve_open_to_accepted(conn: sqlite3.Connection) -> None:
    dispute_id = insert_dispute(
        conn,
        workspace_id="ws",
        target_kind="decision",
        target_id="dec_x",
        claimant_agent_id="agent_a",
        claim_text="claim text",
    )
    flipped = resolve_dispute(
        conn,
        dispute_id=dispute_id,
        new_status="accepted",
        resolution="reviewed: dispute valid; archived dec_x",
    )
    assert flipped is True
    d = get_dispute(conn, dispute_id=dispute_id)
    assert d is not None
    assert d.status == "accepted"
    assert d.resolution.startswith("reviewed")
    assert d.resolved_at  # non-empty timestamp


def test_resolve_idempotent_after_terminal(conn: sqlite3.Connection) -> None:
    """Re-resolving an already-terminal row → no-op (flipped=False),
    but the row's existing terminal state is preserved."""
    dispute_id = insert_dispute(
        conn,
        workspace_id="ws",
        target_kind="decision",
        target_id="dec_y",
        claimant_agent_id="a",
        claim_text="c",
    )
    resolve_dispute(conn, dispute_id=dispute_id, new_status="rejected", resolution="r1")
    second = resolve_dispute(conn, dispute_id=dispute_id, new_status="accepted", resolution="r2")
    assert second is False
    d = get_dispute(conn, dispute_id=dispute_id)
    assert d is not None
    assert d.status == "rejected"  # original terminal preserved
    assert d.resolution == "r1"


def test_resolve_with_invalid_status_raises(conn: sqlite3.Connection) -> None:
    dispute_id = insert_dispute(
        conn,
        workspace_id="ws",
        target_kind="decision",
        target_id="z",
        claimant_agent_id="a",
        claim_text="c",
    )
    with pytest.raises(ValueError, match="new_status must be one of"):
        resolve_dispute(conn, dispute_id=dispute_id, new_status="bogus")


def test_list_filters_by_status(conn: sqlite3.Connection) -> None:
    open_id = insert_dispute(
        conn,
        workspace_id="ws",
        target_kind="decision",
        target_id="d1",
        claimant_agent_id="a",
        claim_text="open one",
    )
    accept_id = insert_dispute(
        conn,
        workspace_id="ws",
        target_kind="decision",
        target_id="d2",
        claimant_agent_id="a",
        claim_text="will accept",
    )
    resolve_dispute(conn, dispute_id=accept_id, new_status="accepted")
    open_only = list_disputes(conn, workspace_id="ws", status="open")
    accepted_only = list_disputes(conn, workspace_id="ws", status="accepted")
    assert [d.id for d in open_only] == [open_id]
    assert [d.id for d in accepted_only] == [accept_id]


def test_list_workspace_isolation(conn: sqlite3.Connection) -> None:
    insert_dispute(
        conn,
        workspace_id="ws_a",
        target_kind="decision",
        target_id="d",
        claimant_agent_id="a",
        claim_text="c",
    )
    insert_dispute(
        conn,
        workspace_id="ws_b",
        target_kind="decision",
        target_id="d",
        claimant_agent_id="a",
        claim_text="c",
    )
    a_rows = list_disputes(conn, workspace_id="ws_a")
    b_rows = list_disputes(conn, workspace_id="ws_b")
    assert len(a_rows) == 1
    assert len(b_rows) == 1
    assert a_rows[0].workspace_id == "ws_a"
    assert b_rows[0].workspace_id == "ws_b"


def test_list_failure_soft_when_table_missing() -> None:
    """Pre-migration DB without memory_disputes → list returns []."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        assert list_disputes(c, workspace_id="ws") == []
        assert get_dispute(c, dispute_id="anything") is None
    finally:
        c.close()


def test_get_dispute_miss_returns_none(conn: sqlite3.Connection) -> None:
    assert get_dispute(conn, dispute_id="nope_does_not_exist") is None


def test_evidence_malformed_json_recovered(conn: sqlite3.Connection) -> None:
    """If evidence_json column is somehow garbled, the reader returns
    an empty list rather than raising."""
    dispute_id = insert_dispute(
        conn,
        workspace_id="ws",
        target_kind="decision",
        target_id="d",
        claimant_agent_id="a",
        claim_text="c",
    )
    conn.execute(
        "UPDATE memory_disputes SET evidence_json = ? WHERE id = ?",
        ("garbage not json", dispute_id),
    )
    conn.commit()
    d = get_dispute(conn, dispute_id=dispute_id)
    assert d is not None
    assert d.evidence == []
