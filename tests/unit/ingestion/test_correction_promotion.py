"""Regression tests for the v1.2.1 hardening of v1.10 promote-flow.

Each test corresponds to one fix from the post-1.2.0 audit pass:

* ``test_promote_atomicity_rollback_on_step3_failure`` — C2 in
  ``correction_promotion.py``: failure inside the candidate-flip step
  must roll back the behavior_instruction write so the operator never
  observes "rule live but candidate still status='new'".
* ``test_coerce_applies_to_*`` — H1 in
  ``correction_promotion_guards.py``: tuple is now accepted (was
  silently dropped to empty tuple); unknown types raise TypeError
  instead of silently coercing to empty.
* ``test_record_throttle_rejection_does_not_commit_outer_tx`` — H2 in
  ``correction_distill.py``: removed explicit ``conn.commit()`` so the
  helper is safe to call from inside a ``with_tx`` block in any future
  refactor of the auto_promote path.
* ``test_with_tx_uses_savepoint_when_nested`` — locks the
  ``db/transactions.py`` change that made the C2 rewrite possible.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from agent_memory_lite.db.connection import open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.extraction.correction_distill import record_throttle_rejection
from agent_memory_lite.ingestion.correction_promotion import (
    CorrectionPromoteInput,
    CorrectionPromotionError,
    promote_correction_to_behavior,
)
from agent_memory_lite.ingestion.correction_promotion_guards import coerce_applies_to


@pytest.fixture
def conn(tmp_path: Any) -> Iterator[sqlite3.Connection]:
    """Real SQLite with the full schema applied — promotion needs every table."""
    db_path = tmp_path / "memory.db"
    c = open_connection(str(db_path))
    apply_migrations(c)
    yield c
    c.close()


def _seed_correction_candidate(
    conn: sqlite3.Connection,
    *,
    workspace_id: str = "ws-promote",
    candidate_id: str = "cand_atomicity",
    subject: str = "Verify before claiming: rollback test",
) -> None:
    """Insert a minimal CORRECTION-kind candidate + its source episode."""
    now = "2026-05-04T12:00:00+00:00"
    conn.execute(
        """
        INSERT OR IGNORE INTO episodes (
            id, workspace_id, source_type, raw_text, trust_level,
            importance, confidence, created_at, metadata_json
        ) VALUES ('ep_seed', ?, 'user_message', 'placeholder', 'user_asserted',
                  0.7, 1.0, ?, '{}')
        """,
        (workspace_id, now),
    )
    conn.execute(
        """
        INSERT INTO memory_candidates (
            id, workspace_id, kind, subject, predicate, object, evidence,
            confidence, importance, trust_level, status,
            temporal_json, write_targets_json, metadata_json,
            source_episode_id, created_at, updated_at
        ) VALUES (?, ?, 'correction', ?, 'should_avoid', NULL,
                  'agent claim: x\nuser correction: y',
                  0.85, 0.85, 'user_asserted', 'new',
                  ?, '["behavior_instruction","audit"]', '{}',
                  'ep_seed', ?, ?)
        """,
        (
            candidate_id,
            workspace_id,
            subject,
            '{"observed_at":"' + now + '","valid_from":"' + now + '"}',
            now,
            now,
        ),
    )


# ---------- C2: atomicity ---------------------------------------------------


def test_promote_atomicity_rollback_on_step3_failure(conn: sqlite3.Connection) -> None:
    """C2 lock: when the candidate-flip step (the LAST write in the
    promote pipeline) raises, the behavior_instruction MUST be rolled
    back. Pre-1.2.1 the three writes had separate commits — a Step-3
    failure left the rule live but the candidate status='new', causing
    a 409 ``name_taken`` on retry. The fix wraps all three in one
    ``with_tx``; this test patches ``insert_audit`` (called inside
    Step 3) to raise, then asserts NO behavior_instruction exists and
    the candidate stayed status='new'."""
    _seed_correction_candidate(conn)
    payload = CorrectionPromoteInput(
        workspace_id="ws-promote",
        candidate_id="cand_atomicity",
        name="rollback-test-rule",
    )
    # Patch insert_audit ONLY inside the promotion module. The
    # behavior_writer's audit (Step 1) uses its own import, which is
    # not patched — we want Step 3 to fail, not Step 1.
    with (
        patch(
            "agent_memory_lite.ingestion.correction_promotion.insert_audit",
            side_effect=RuntimeError("simulated step-3 failure"),
        ),
        pytest.raises(RuntimeError, match="simulated step-3 failure"),
    ):
        promote_correction_to_behavior(conn, payload)

    # No behavior_instruction with that name should exist.
    row = conn.execute(
        "SELECT id FROM behavior_instructions WHERE workspace_id=? AND name=?",
        ("ws-promote", "rollback-test-rule"),
    ).fetchone()
    assert row is None, "behavior_instruction was not rolled back on step-3 failure"

    # The candidate's status must still be 'new' so the operator can retry.
    row = conn.execute(
        "SELECT status, promoted_target_id FROM memory_candidates WHERE id=?",
        ("cand_atomicity",),
    ).fetchone()
    assert row is not None
    assert row["status"] == "new"
    assert row["promoted_target_id"] is None


def test_promote_atomicity_rollback_on_step1_failure(conn: sqlite3.Connection) -> None:
    """C2 lock (Step 1): when ``upsert_behavior_instruction`` raises
    (e.g. integrity violation), the candidate must remain ``status='new'``
    and no behavior_instruction with that name should exist. The outer
    ``with_tx`` swallows the partial Step-1 SAVEPOINT writes."""
    _seed_correction_candidate(conn, candidate_id="cand_step1_fail")
    payload = CorrectionPromoteInput(
        workspace_id="ws-promote",
        candidate_id="cand_step1_fail",
        name="step1-fail-rule",
    )
    with (
        patch(
            "agent_memory_lite.ingestion.correction_promotion.upsert_behavior_instruction",
            side_effect=RuntimeError("simulated step-1 failure"),
        ),
        pytest.raises(RuntimeError, match="simulated step-1 failure"),
    ):
        promote_correction_to_behavior(conn, payload)

    row = conn.execute(
        "SELECT id FROM behavior_instructions WHERE name=?",
        ("step1-fail-rule",),
    ).fetchone()
    assert row is None
    cand = conn.execute(
        "SELECT status FROM memory_candidates WHERE id=?",
        ("cand_step1_fail",),
    ).fetchone()
    assert cand["status"] == "new"


def test_promote_atomicity_rollback_on_step2_pin_failure(conn: sqlite3.Connection) -> None:
    """C2 lock (Step 2): when the optional pin step raises, the
    behavior_instruction write from Step 1 must be rolled back AND
    the candidate must remain ``status='new'``. Pre-1.2.1 Step 1
    committed first, so a Step 2 failure left a non-pinned but live
    behavior_instruction with the candidate stuck."""
    _seed_correction_candidate(conn, candidate_id="cand_step2_fail")
    payload = CorrectionPromoteInput(
        workspace_id="ws-promote",
        candidate_id="cand_step2_fail",
        name="step2-fail-rule",
        pinned=True,
    )
    with (
        patch(
            "agent_memory_lite.ingestion.correction_promotion.pin_memory_object",
            side_effect=RuntimeError("simulated step-2 pin failure"),
        ),
        pytest.raises(RuntimeError, match="simulated step-2 pin failure"),
    ):
        promote_correction_to_behavior(conn, payload)

    # behavior_instruction created in Step 1 must NOT survive — the
    # outer ``with_tx`` rolls back the inner SAVEPOINT.
    row = conn.execute(
        "SELECT id FROM behavior_instructions WHERE name=?",
        ("step2-fail-rule",),
    ).fetchone()
    assert row is None, "Step 1 behavior_instruction was not rolled back when Step 2 (pin) failed"
    cand = conn.execute(
        "SELECT status FROM memory_candidates WHERE id=?",
        ("cand_step2_fail",),
    ).fetchone()
    assert cand["status"] == "new"


def test_promote_succeeds_atomically_on_happy_path(conn: sqlite3.Connection) -> None:
    """Sanity: with the new transactional wrapper, a clean promote
    still produces ALL of: behavior_instruction live, candidate flipped,
    audit row written, no orphaned partial state."""
    _seed_correction_candidate(conn, candidate_id="cand_happy")
    payload = CorrectionPromoteInput(
        workspace_id="ws-promote",
        candidate_id="cand_happy",
        name="happy-path-rule",
    )
    instruction = promote_correction_to_behavior(conn, payload)
    assert instruction.id
    # behavior_instruction live
    bi = conn.execute(
        "SELECT id, source_type, source_id FROM behavior_instructions WHERE name=?",
        ("happy-path-rule",),
    ).fetchone()
    assert bi is not None
    assert bi["source_type"] == "memory_candidate"
    assert bi["source_id"] == "cand_happy"
    # candidate flipped
    cand = conn.execute(
        "SELECT status, promoted_target_id FROM memory_candidates WHERE id=?",
        ("cand_happy",),
    ).fetchone()
    assert cand["status"] == "promoted"
    assert cand["promoted_target_id"] == bi["id"]
    # audit row written
    audit = conn.execute(
        "SELECT id FROM audit_log "
        "WHERE action='memory_candidate.promoted_to_behavior' AND target_id=?",
        ("cand_happy",),
    ).fetchone()
    assert audit is not None


# ---------- H1: coerce_applies_to type-handling ----------------------------


def test_coerce_applies_to_accepts_tuple_unchanged() -> None:
    """H1 lock: pre-1.2.1, ``isinstance(value, list)`` was the only
    branch — tuple silently fell through to ``return ()``. A caller
    that passed an already-coerced tuple lost the contents. Tuple is
    now accepted alongside list."""
    assert coerce_applies_to(("a", "b")) == ("a", "b")
    assert coerce_applies_to(()) == ()


def test_coerce_applies_to_raises_on_unknown_type() -> None:
    """H1 lock: dict / int / object now raise TypeError instead of
    silently returning ``()``. Surfaces bad input as a 422 at the route
    boundary rather than a confusing empty-list-with-no-warning."""
    with pytest.raises(TypeError, match="applies_to"):
        coerce_applies_to({"not": "supported"})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="applies_to"):
        coerce_applies_to(42)  # type: ignore[arg-type]


def test_coerce_applies_to_str_and_list_unchanged() -> None:
    """Sanity: 1-tuple from str, list-of-str → tuple. Behaviour
    unchanged from 1.2.0."""
    assert coerce_applies_to("single") == ("single",)
    assert coerce_applies_to(["a", "b"]) == ("a", "b")
    assert coerce_applies_to(None) == ()


# ---------- H2: record_throttle_rejection no shared-conn commit ------------


def test_record_throttle_rejection_does_not_commit_outer_tx(
    conn: sqlite3.Connection,
) -> None:
    """H2 lock: pre-1.2.1, ``record_throttle_rejection`` called
    ``conn.commit()`` after writing its audit row. If a future caller
    invoked it from inside a ``with_tx`` block, that explicit commit
    would have prematurely ended the outer transaction, persisting any
    partial in-flight writes. The 1.2.1 fix removes the commit; this
    test simulates the dangerous path: open an outer tx, write a
    sentinel row, call record_throttle_rejection, then ROLLBACK.
    With the bug, the rollback would no-op (because the throttle
    helper's commit already finalized the sentinel). With the fix,
    the rollback discards both the sentinel AND the throttle row."""
    # Manual BEGIN — caller takes responsibility for COMMIT/ROLLBACK.
    conn.execute("BEGIN")
    try:
        # Sentinel write inside the outer tx.
        conn.execute(
            "INSERT INTO episodes (id, workspace_id, source_type, raw_text, "
            "trust_level, importance, confidence, created_at, metadata_json) "
            "VALUES ('ep_sentinel', 'ws-throttle', 'user_message', 'sentinel', "
            "'user_asserted', 0.5, 1.0, '2026-05-04T12:00:00+00:00', '{}')"
        )
        record_throttle_rejection(
            conn,
            workspace_id="ws-throttle",
            episode_id="ep_sentinel",
            max_per_day=20,
        )
        # Discard everything — sentinel + throttle row.
        conn.execute("ROLLBACK")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    # Both writes must be gone.
    row = conn.execute("SELECT id FROM episodes WHERE id='ep_sentinel'").fetchone()
    assert row is None, (
        "sentinel survived rollback — record_throttle_rejection committed prematurely"
    )
    row = conn.execute(
        "SELECT id FROM audit_log "
        "WHERE action='extraction.correction_rejected_throttled' "
        "AND target_id='ep_sentinel'"
    ).fetchone()
    assert row is None, "throttle audit row survived outer rollback"


# ---------- with_tx savepoint composability (powers C2) --------------------


def test_with_tx_uses_savepoint_when_nested(conn: sqlite3.Connection) -> None:
    """1.2.1 lock: pre-fix, a nested ``with_tx`` raised
    ``OperationalError: cannot start a transaction within a
    transaction``. The fix uses SAVEPOINTs when ``conn.in_transaction``
    is True. This test enters two with_tx blocks; without the fix it
    would raise on the inner BEGIN."""
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, "
        "trust_level, importance, confidence, created_at, metadata_json) "
        "VALUES ('ep_nested_outer', 'ws-nest', 'user_message', 'outer', "
        "'user_asserted', 0.5, 1.0, '2026-05-04T12:00:00+00:00', '{}')"
    )
    with with_tx(conn):
        conn.execute(
            "INSERT INTO episodes (id, workspace_id, source_type, raw_text, "
            "trust_level, importance, confidence, created_at, metadata_json) "
            "VALUES ('ep_outer_tx', 'ws-nest', 'user_message', 'outer-tx', "
            "'user_asserted', 0.5, 1.0, '2026-05-04T12:00:01+00:00', '{}')"
        )
        with with_tx(conn):
            conn.execute(
                "INSERT INTO episodes (id, workspace_id, source_type, raw_text, "
                "trust_level, importance, confidence, created_at, metadata_json) "
                "VALUES ('ep_inner_savepoint', 'ws-nest', 'user_message', "
                "'inner-savepoint', 'user_asserted', 0.5, 1.0, "
                "'2026-05-04T12:00:02+00:00', '{}')"
            )
    # All three writes survived: nesting works.
    rows = conn.execute(
        "SELECT id FROM episodes WHERE workspace_id='ws-nest' ORDER BY id"
    ).fetchall()
    assert {r["id"] for r in rows} == {
        "ep_nested_outer",
        "ep_outer_tx",
        "ep_inner_savepoint",
    }


def _inner_savepoint_then_raise(conn: sqlite3.Connection) -> None:
    """Helper: open a nested with_tx, write a row, raise — used by
    test_with_tx_savepoint_rolls_back_on_inner_failure to keep the
    pytest.raises block at one statement (PT012)."""
    with with_tx(conn):
        conn.execute(
            "INSERT INTO episodes (id, workspace_id, source_type, raw_text, "
            "trust_level, importance, confidence, created_at, metadata_json) "
            "VALUES ('ep_inner_discarded', 'ws-sp', 'user_message', 'discard', "
            "'user_asserted', 0.5, 1.0, '2026-05-04T12:00:01+00:00', '{}')"
        )
        raise RuntimeError("inner-fail")


def test_with_tx_savepoint_rolls_back_on_inner_failure(
    conn: sqlite3.Connection,
) -> None:
    """1.2.1 lock: an exception inside a nested ``with_tx`` rolls back
    ONLY to the savepoint. The outer transaction continues and its
    own writes are preserved on outer COMMIT."""
    with with_tx(conn):
        conn.execute(
            "INSERT INTO episodes (id, workspace_id, source_type, raw_text, "
            "trust_level, importance, confidence, created_at, metadata_json) "
            "VALUES ('ep_outer_kept', 'ws-sp', 'user_message', 'kept', "
            "'user_asserted', 0.5, 1.0, '2026-05-04T12:00:00+00:00', '{}')"
        )
        with pytest.raises(RuntimeError, match="inner-fail"):
            _inner_savepoint_then_raise(conn)
    # The outer write survived; the inner one was rolled back to the savepoint.
    rows = {
        r["id"]
        for r in conn.execute("SELECT id FROM episodes WHERE workspace_id='ws-sp'").fetchall()
    }
    assert "ep_outer_kept" in rows
    assert "ep_inner_discarded" not in rows


def test_promote_wrong_kind_still_raises_correction_error(
    conn: sqlite3.Connection,
) -> None:
    """Sanity: refactor must not regress existing guards.
    A non-CORRECTION candidate raises before ANY write happens."""
    now = "2026-05-04T12:00:00+00:00"
    conn.execute(
        "INSERT OR IGNORE INTO episodes (id, workspace_id, source_type, "
        "raw_text, trust_level, importance, confidence, created_at, "
        "metadata_json) VALUES ('ep_seed', 'ws-promote', 'user_message', "
        "'placeholder', 'user_asserted', 0.7, 1.0, ?, '{}')",
        (now,),
    )
    conn.execute(
        """
        INSERT INTO memory_candidates (
            id, workspace_id, kind, subject, predicate, object, evidence,
            confidence, importance, trust_level, status,
            temporal_json, write_targets_json, metadata_json,
            source_episode_id, created_at, updated_at
        ) VALUES ('cand_decision', 'ws-promote', 'decision', 'wrong kind',
                  'about', NULL, 'evidence', 0.85, 0.85, 'user_asserted',
                  'new', '{}', '["decision"]', '{}', 'ep_seed', ?, ?)
        """,
        (now, now),
    )
    payload = CorrectionPromoteInput(
        workspace_id="ws-promote",
        candidate_id="cand_decision",
        name="should-not-promote",
    )
    with pytest.raises(CorrectionPromotionError) as exc_info:
        promote_correction_to_behavior(conn, payload)
    assert exc_info.value.code == "wrong_kind"
    # No behavior_instruction created (guard fired before any write).
    row = conn.execute(
        "SELECT id FROM behavior_instructions WHERE name='should-not-promote'"
    ).fetchone()
    assert row is None
