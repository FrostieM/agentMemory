"""Promotion bridge: emit candidate when validated theory has enough evidence.

Crucial invariant — bridge NEVER touches the `decisions` table directly.
That guarantee is asserted explicitly here so trust-gate regressions are
caught at the unit level.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.theories.promotion_bridge import maybe_emit_decision_candidate


def _settings(*, enabled: bool = True, min_evidence: int = 3) -> Settings:
    """Build a Settings object directly bypassing env (tests run isolated)."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_THEORY_BRIDGE_ENABLED="true" if enabled else "false",
        MEMORY_THEORY_BRIDGE_MIN_EVIDENCE=str(min_evidence),
    )


def _seed_theory(
    conn: sqlite3.Connection,
    *,
    theory_id: str,
    status: str,
    confidence: float = 0.7,
    title: str = "T",
    claim: str = "Claim body",
) -> None:
    conn.execute(
        """
        INSERT INTO theories
        (id, workspace_id, title, domain, claim, mechanism, predictions_json,
         experiment_plan, tags_json, status, supersedes_theory_id,
         source_episode_id, confidence, importance, created_at, updated_at,
         last_tested_at, validation_criteria_json,
         dependent_decision_ids_json, evidence_count, evidence_strength)
        VALUES (?, 'default', ?, 'general', ?, NULL, '[]', NULL, '[]', ?,
                NULL, NULL, ?, 0.6, '2025-01-01T00:00:00Z',
                '2025-01-01T00:00:00Z', NULL, '[]', '[]', 0, 0.0)
        """,
        (theory_id, title, claim, status, confidence),
    )


def _seed_evidence(
    conn: sqlite3.Connection,
    *,
    theory_id: str,
    evidence_id: str,
    kind: str = "supporting",
    confidence: float = 0.8,
) -> None:
    conn.execute(
        """
        INSERT INTO theory_evidence
        (id, workspace_id, theory_id, kind, summary, source_episode_id,
         artifact_path, metrics_json, confidence, observed_at, created_at)
        VALUES (?, 'default', ?, ?, 'summary', NULL, NULL, '{}', ?,
                '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (evidence_id, theory_id, kind, confidence),
    )


def _decisions_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()
    return int(row[0])


def _candidates_count(conn: sqlite3.Connection, *, status: str = "pending") -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM candidates WHERE kind = 'decision' AND status = ?", (status,)
    ).fetchone()
    return int(row[0])


def test_disabled_flag_returns_none(applied_conn: sqlite3.Connection) -> None:
    _seed_theory(applied_conn, theory_id="th_a", status="validated")
    settings = _settings(enabled=False)
    result = maybe_emit_decision_candidate(
        applied_conn, workspace_id="default", theory_id="th_a", settings=settings
    )
    assert result is None


def test_unknown_theory_returns_none(applied_conn: sqlite3.Connection) -> None:
    settings = _settings()
    result = maybe_emit_decision_candidate(
        applied_conn, workspace_id="default", theory_id="th_missing", settings=settings
    )
    assert result is None


def test_non_validated_theory_returns_none(applied_conn: sqlite3.Connection) -> None:
    _seed_theory(applied_conn, theory_id="th_test", status="testing")
    settings = _settings()
    result = maybe_emit_decision_candidate(
        applied_conn, workspace_id="default", theory_id="th_test", settings=settings
    )
    assert result is None


def test_below_evidence_threshold_returns_none(applied_conn: sqlite3.Connection) -> None:
    _seed_theory(applied_conn, theory_id="th_a", status="validated")
    _seed_evidence(applied_conn, theory_id="th_a", evidence_id="ev_1")
    settings = _settings(min_evidence=3)  # only 1 evidence row, threshold 3
    result = maybe_emit_decision_candidate(
        applied_conn, workspace_id="default", theory_id="th_a", settings=settings
    )
    assert result is None


def test_threshold_met_emits_candidate_but_not_decision(
    applied_conn: sqlite3.Connection,
) -> None:
    _seed_theory(applied_conn, theory_id="th_a", status="validated", confidence=0.9)
    for i in range(3):
        _seed_evidence(applied_conn, theory_id="th_a", evidence_id=f"ev_{i}")
    decisions_before = _decisions_count(applied_conn)
    settings = _settings(min_evidence=3)
    result = maybe_emit_decision_candidate(
        applied_conn, workspace_id="default", theory_id="th_a", settings=settings
    )
    assert result is not None
    assert result.evidence_count == 3
    assert _candidates_count(applied_conn, status="new") == 1
    # Crucial invariant — the bridge MUST NOT have written a row to decisions.
    assert _decisions_count(applied_conn) == decisions_before


def test_idempotent_when_pending_candidate_exists(
    applied_conn: sqlite3.Connection,
) -> None:
    _seed_theory(applied_conn, theory_id="th_a", status="validated")
    for i in range(3):
        _seed_evidence(applied_conn, theory_id="th_a", evidence_id=f"ev_{i}")
    settings = _settings(min_evidence=3)
    first = maybe_emit_decision_candidate(
        applied_conn, workspace_id="default", theory_id="th_a", settings=settings
    )
    second = maybe_emit_decision_candidate(
        applied_conn, workspace_id="default", theory_id="th_a", settings=settings
    )
    assert first is not None
    assert second is None
    assert _candidates_count(applied_conn, status="new") == 1


def test_only_supporting_evidence_counts_toward_threshold(
    applied_conn: sqlite3.Connection,
) -> None:
    _seed_theory(applied_conn, theory_id="th_a", status="validated")
    _seed_evidence(applied_conn, theory_id="th_a", evidence_id="ev_1", kind="supporting")
    _seed_evidence(applied_conn, theory_id="th_a", evidence_id="ev_2", kind="refuting")
    _seed_evidence(applied_conn, theory_id="th_a", evidence_id="ev_3", kind="neutral")
    settings = _settings(min_evidence=2)
    result = maybe_emit_decision_candidate(
        applied_conn, workspace_id="default", theory_id="th_a", settings=settings
    )
    # Only 1 supporting row; threshold is 2 -> no emission.
    assert result is None


def test_audit_row_written_on_emission(applied_conn: sqlite3.Connection) -> None:
    _seed_theory(applied_conn, theory_id="th_a", status="validated")
    for i in range(3):
        _seed_evidence(applied_conn, theory_id="th_a", evidence_id=f"ev_{i}")
    settings = _settings(min_evidence=3)
    maybe_emit_decision_candidate(
        applied_conn, workspace_id="default", theory_id="th_a", settings=settings
    )
    audit_actions = applied_conn.execute(
        "SELECT action FROM audit_log WHERE target_type = 'memory_candidate'"
    ).fetchall()
    assert [str(row[0]) for row in audit_actions] == ["theory.candidate_decision_emitted"]
