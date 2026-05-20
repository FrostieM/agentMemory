"""Regression: ``row_to_evidence`` tolerates unknown ``kind`` strings.

The v3.4 autonomous loop bypassed the ``add_theory_evidence`` repo
helper and INSERTed a literal ``'autonomous_corroboration'`` string
before that value existed in ``TheoryEvidenceKind``. Once one such
row landed in the DB, every ``/memory/get_context`` that gathered the
parent theory crashed with ``ValueError`` and returned HTTP 500 — for
~2.5h on the copyBot workspace before the bug was caught.

Two defenses are now in place; this test locks both:

1. ``AUTONOMOUS_CORROBORATION`` is a first-class enum value, so legit
   rows round-trip cleanly without falling through to the fallback.
2. ``_coerce_evidence_kind`` falls back to ``NEUTRAL`` for any other
   unknown string a future raw-SQL writer might introduce, instead of
   letting the exception escape the read path.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.enums import TheoryEvidenceKind
from agent_memory_lite.repositories.theories_search import (
    _coerce_evidence_kind,
    row_to_evidence,
)


def test_autonomous_corroboration_is_first_class_enum_value() -> None:
    """The literal the autonomous_loop writes must be a real enum
    value, not silently rewritten to NEUTRAL — otherwise the UI loses
    the ability to filter agent-derived evidence from human-derived."""
    assert TheoryEvidenceKind("autonomous_corroboration") is (
        TheoryEvidenceKind.AUTONOMOUS_CORROBORATION
    )
    assert TheoryEvidenceKind.AUTONOMOUS_CORROBORATION.value == "autonomous_corroboration"


def test_coerce_kind_passes_through_known_values() -> None:
    """Known enum values must round-trip without going through the
    NEUTRAL fallback (we want the actual semantics preserved)."""
    for kind in TheoryEvidenceKind:
        assert _coerce_evidence_kind(kind.value) is kind


def test_coerce_kind_accepts_existing_enum_instance() -> None:
    """Defensive: callers may already pass an enum instance."""
    assert _coerce_evidence_kind(TheoryEvidenceKind.SUPPORTING) is (TheoryEvidenceKind.SUPPORTING)


def test_coerce_kind_falls_back_to_neutral_on_unknown() -> None:
    """An unknown ``kind`` string must NOT raise — that was the bug
    that returned HTTP 500 on every get_context."""
    assert _coerce_evidence_kind("some_future_kind_we_dont_know_yet") is (
        TheoryEvidenceKind.NEUTRAL
    )
    # Non-string inputs too (defensive: row["kind"] could be NULL in a
    # corrupt row; we want NEUTRAL, not crash).
    assert _coerce_evidence_kind(None) is TheoryEvidenceKind.NEUTRAL
    assert _coerce_evidence_kind(42) is TheoryEvidenceKind.NEUTRAL


def test_row_to_evidence_handles_unknown_kind(applied_conn: sqlite3.Connection) -> None:
    """End-to-end: a DB row with an unknown ``kind`` value must parse
    via ``row_to_evidence`` without raising — instead of returning
    HTTP 500 through the read path that crashed copyBot's session."""
    workspace = "evidence-kind-tolerance-ws"
    # Theory + evidence rows via raw SQL because we WANT to write a
    # string the enum doesn't know about (the bug we're guarding).
    applied_conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, domain, claim, mechanism,
            predictions_json, validation_criteria_json, experiment_plan,
            tags_json, status, confidence, importance, source_episode_id,
            evidence_count, evidence_strength, created_at, updated_at)
           VALUES ('th_test', ?, 'test', 'd', 'c', '', '[]', '[]', '',
                   '[]', 'proposed', 0.5, 0.5, NULL, 0, 0.0,
                   '2026-05-20T00:00:00+00:00',
                   '2026-05-20T00:00:00+00:00')""",
        (workspace,),
    )
    applied_conn.execute(
        """INSERT INTO theory_evidence
           (id, workspace_id, theory_id, kind, summary,
            source_episode_id, artifact_path, metrics_json,
            confidence, observed_at, created_at)
           VALUES ('thev_test', ?, 'th_test', 'totally_unknown_kind',
                   'a', NULL, NULL, '{}', 1.0,
                   '2026-05-20T00:00:00+00:00',
                   '2026-05-20T00:00:00+00:00')""",
        (workspace,),
    )
    applied_conn.commit()
    row = applied_conn.execute(
        "SELECT * FROM theory_evidence WHERE id = 'thev_test'",
    ).fetchone()
    assert row is not None
    evidence = row_to_evidence(row)
    # Did NOT raise; mapped to NEUTRAL as the safe default.
    assert evidence.kind is TheoryEvidenceKind.NEUTRAL
    assert evidence.id == "thev_test"


def test_row_to_evidence_preserves_autonomous_corroboration(
    applied_conn: sqlite3.Connection,
) -> None:
    """Real-world case: rows already in production carry
    ``kind='autonomous_corroboration'``. After the enum gained the
    value, they must round-trip as themselves — not silently downgrade
    to NEUTRAL through the fallback."""
    workspace = "evidence-autonomous-roundtrip-ws"
    applied_conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, domain, claim, mechanism,
            predictions_json, validation_criteria_json, experiment_plan,
            tags_json, status, confidence, importance, source_episode_id,
            evidence_count, evidence_strength, created_at, updated_at)
           VALUES ('th_auto', ?, 'auto', 'd', 'c', '', '[]', '[]', '',
                   '[]', 'proposed', 0.7, 0.6, NULL, 1, 0.7,
                   '2026-05-20T00:00:00+00:00',
                   '2026-05-20T00:00:00+00:00')""",
        (workspace,),
    )
    applied_conn.execute(
        """INSERT INTO theory_evidence
           (id, workspace_id, theory_id, kind, summary,
            source_episode_id, artifact_path, metrics_json,
            confidence, observed_at, created_at)
           VALUES ('thev_auto', ?, 'th_auto', 'autonomous_corroboration',
                   'token-overlap match in episode corpus',
                   NULL, NULL, '{}', 0.7,
                   '2026-05-20T00:00:00+00:00',
                   '2026-05-20T00:00:00+00:00')""",
        (workspace,),
    )
    applied_conn.commit()
    row = applied_conn.execute(
        "SELECT * FROM theory_evidence WHERE id = 'thev_auto'",
    ).fetchone()
    assert row is not None
    evidence = row_to_evidence(row)
    assert evidence.kind is TheoryEvidenceKind.AUTONOMOUS_CORROBORATION
