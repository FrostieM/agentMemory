"""Phase 1: lint._prior_failures surfaces low-outcome decisions/behaviors."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.cognition.lint import _prior_failures
from agent_memory_lite.utils.time import iso_now


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


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    id_: str,
    title: str,
    gist: str,
    outcome: float = 0.0,
    status: str = "active",
) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, 'ws', ?, 'body', ?, ?, ?, ?, ?, ?, 0)""",
        (id_, title, gist, status, iso_now(), iso_now(), iso_now(), outcome),
    )
    conn.commit()


def _seed_behavior(
    conn: sqlite3.Connection,
    *,
    id_: str,
    name: str,
    rule_one_line: str,
    outcome: float = 0.0,
    active: int = 1,
) -> None:
    conn.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, scope, priority, rule, rule_one_line,
            applies_to_json, active, pinned, created_at, updated_at, outcome_score)
           VALUES (?, 'ws', ?, 'operating_rule', 'workspace', 'project_convention',
                   'rule body', ?, '[]', ?, 0, ?, ?, ?)""",
        (id_, name, rule_one_line, active, iso_now(), iso_now(), outcome),
    )
    conn.commit()


def test_low_outcome_decision_surfaces_as_failure(conn: sqlite3.Connection) -> None:
    _seed_decision(
        conn, id_="dec_kelly", title="Adopt Kelly", gist="full Kelly sizing", outcome=-0.6
    )
    failures = _prior_failures(conn, "ws", "kelly")
    kinds = [f["kind"] for f in failures]
    assert "low_outcome_decision" in kinds
    failure = next(f for f in failures if f["kind"] == "low_outcome_decision")
    assert failure["id"] == "dec_kelly"
    assert failure["outcome_score"] < 0


def test_positive_outcome_decision_does_not_surface(conn: sqlite3.Connection) -> None:
    _seed_decision(
        conn, id_="dec_kelly", title="Adopt Kelly", gist="quarter Kelly sizing", outcome=0.5
    )
    failures = _prior_failures(conn, "ws", "kelly")
    assert failures == []


def test_low_outcome_behavior_surfaces(conn: sqlite3.Connection) -> None:
    _seed_behavior(
        conn, id_="beh_x", name="kelly-cap", rule_one_line="Cap Kelly at quarter", outcome=-0.5
    )
    failures = _prior_failures(conn, "ws", "kelly")
    kinds = [f["kind"] for f in failures]
    assert "low_outcome_behavior" in kinds


def test_threshold_minus_zero_three_is_floor(conn: sqlite3.Connection) -> None:
    """Decisions at outcome=-0.2 must NOT surface (threshold is -0.3)."""
    _seed_decision(conn, id_="dec_borderline", title="Adopt Kelly", gist="x", outcome=-0.2)
    failures = _prior_failures(conn, "ws", "kelly")
    assert not any(f["id"] == "dec_borderline" for f in failures)


def _seed_insight(
    conn: sqlite3.Connection,
    *,
    id_: str,
    summary: str,
    insight_type: str = "consolidation",
    outcome: float = 0.0,
    status: str = "candidate",
) -> None:
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, status, confidence,
            outcome_score, created_at, updated_at)
           VALUES (?, 'ws', ?, ?, ?, ?, 0.6, ?, ?, ?)""",
        (id_, insight_type, summary, summary[:60], status, outcome, iso_now(), iso_now()),
    )
    conn.commit()


# ----- discredited rows must NOT surface as live "we tried this and it failed" -----


def test_archived_decision_does_not_surface_as_failure(conn: sqlite3.Connection) -> None:
    """compute_outcome pins archived to -1.0; without the status guard a RETIRED
    decision would be the headline 'prior failure' the agent reads on the hook."""
    _seed_decision(
        conn, id_="dec_old", title="Adopt Kelly", gist="kelly", outcome=-1.0, status="archived"
    )
    failures = _prior_failures(conn, "ws", "kelly")
    assert not any(f["id"] == "dec_old" for f in failures)


def test_superseded_decision_does_not_surface_as_failure(conn: sqlite3.Connection) -> None:
    _seed_decision(
        conn, id_="dec_sup", title="Adopt Kelly", gist="kelly", outcome=-0.5, status="superseded"
    )
    failures = _prior_failures(conn, "ws", "kelly")
    assert not any(f["id"] == "dec_sup" for f in failures)


def test_deactivated_behavior_does_not_surface_as_failure(conn: sqlite3.Connection) -> None:
    """A deactivated good rule (e.g. a retired safety behavior) must not be
    presented as a known failure that nudges the agent away from it."""
    _seed_behavior(
        conn, id_="beh_off", name="kelly-cap", rule_one_line="Cap Kelly", outcome=-1.0, active=0
    )
    failures = _prior_failures(conn, "ws", "kelly")
    assert not any(f["id"] == "beh_off" for f in failures)


def test_rejected_insight_does_not_surface_as_failure(conn: sqlite3.Connection) -> None:
    _seed_insight(
        conn,
        id_="ins_rej",
        summary="kelly contradiction",
        insight_type="contradiction",
        outcome=-0.5,
        status="rejected",
    )
    failures = _prior_failures(conn, "ws", "kelly")
    assert not any(f["id"] == "ins_rej" for f in failures)


def test_rejected_decision_does_not_surface_as_failure(conn: sqlite3.Connection) -> None:
    """'rejected' is a first-class terminal DecisionStatus; the canonical dead
    set is archived/superseded/rejected. The same-function insight arm already
    excludes 'rejected' -- the decision arm must too, or a rejected decision
    leaks as a live 'prior failure' on the hook."""
    _seed_decision(
        conn, id_="dec_rej", title="Adopt Kelly", gist="kelly", outcome=-0.9, status="rejected"
    )
    failures = _prior_failures(conn, "ws", "kelly")
    assert not any(f["id"] == "dec_rej" for f in failures)


def test_mixed_case_terminal_status_does_not_surface(conn: sqlite3.Connection) -> None:
    """The status guards are case-folded. memory_edit does not normalize status,
    so a mixed-case terminal label ('Archived', 'REJECTED') must still be caught
    -- a BINARY-collation NOT IN would let it slip a retired row through."""
    _seed_decision(
        conn,
        id_="dec_arch_mixed",
        title="Adopt Kelly",
        gist="kelly",
        outcome=-0.9,
        status="Archived",
    )
    _seed_insight(
        conn,
        id_="ins_rej_mixed",
        summary="kelly contradiction",
        insight_type="contradiction",
        outcome=-0.5,
        status="REJECTED",
    )
    failures = _prior_failures(conn, "ws", "kelly")
    assert not any(f["id"] == "dec_arch_mixed" for f in failures)
    assert not any(f["id"] == "ins_rej_mixed" for f in failures)


def test_padded_terminal_status_does_not_surface(conn: sqlite3.Connection) -> None:
    """Status guards are whitespace-stripped (LOWER(TRIM(...)), symmetric with
    _is_discredited): a padded terminal status must not slip a retired row
    through. Reachable because memory_edit does not normalize status values."""
    _seed_decision(
        conn,
        id_="dec_pad",
        title="Adopt Kelly",
        gist="kelly",
        outcome=-0.9,
        status="  superseded  ",
    )
    failures = _prior_failures(conn, "ws", "kelly")
    assert not any(f["id"] == "dec_pad" for f in failures)


def test_padded_rejected_theory_still_surfaces(conn: sqlite3.Connection) -> None:
    """The rejected-theory positive selector is also normalized, so a padded /
    mixed-case 'rejected' theory is still caught as a prior failure (no under-
    fetch of a genuine warning)."""
    conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, claim, status, created_at, updated_at)
           VALUES ('th_pad', 'ws', 'Kelly safe', 'kelly is safe', '  Rejected  ', ?, ?)""",
        (iso_now(), iso_now()),
    )
    conn.commit()
    failures = _prior_failures(conn, "ws", "kelly")
    assert any(f["id"] == "th_pad" and f["kind"] == "rejected_theory" for f in failures)


def test_active_low_outcome_decision_still_surfaces(conn: sqlite3.Connection) -> None:
    """Regression guard: the status filter must NOT over-filter -- a genuinely
    active low-outcome decision still surfaces (with its real status)."""
    _seed_decision(
        conn, id_="dec_live", title="Adopt Kelly", gist="kelly", outcome=-0.6, status="active"
    )
    failures = _prior_failures(conn, "ws", "kelly")
    surfaced = next((f for f in failures if f["id"] == "dec_live"), None)
    assert surfaced is not None
    assert surfaced["status"] == "active"


def test_low_outcome_insight_surfaces_as_failure(conn: sqlite3.Connection) -> None:
    """Phase 3: insights with insight_type contradiction/consolidation and
    outcome_score below threshold surface as low_outcome_insight failures."""
    _seed_insight(
        conn,
        id_="ins_kelly_bad",
        summary="kelly sizing pattern contradicted by recent divergence",
        insight_type="contradiction",
        outcome=-0.5,
    )
    failures = _prior_failures(conn, "ws", "kelly")
    kinds = [f["kind"] for f in failures]
    assert "low_outcome_insight" in kinds


def test_handles_pre_migration_db_gracefully() -> None:
    """When outcome_score column does not exist, _prior_failures must not raise."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    # Do NOT apply 0002. _prior_failures should still return rejected theories
    # and skip the outcome queries silently.
    c.execute(
        """INSERT INTO theories
           (id, workspace_id, title, claim, status, created_at, updated_at)
           VALUES ('th_x', 'ws', 'Kelly safe', 'Half-Kelly is safe', 'rejected',
                   ?, ?)""",
        (iso_now(), iso_now()),
    )
    c.commit()
    failures = _prior_failures(c, "ws", "kelly")
    # The rejected theory still surfaces; the outcome queries no-op.
    assert any(f["kind"] == "rejected_theory" for f in failures)
    c.close()
