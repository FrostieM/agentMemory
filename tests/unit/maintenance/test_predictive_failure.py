"""v3.1 Vector 5 — predictive failure detection heuristic."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.maintenance import predictive_failure as pf


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_PREDICTIVE_FAILURE_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_PREDICTIVE_FAILURE_OUTCOME_THRESHOLD", raising=False)
    monkeypatch.delenv("MEMORY_PREDICTIVE_FAILURE_JACCARD_MIN", raising=False)
    monkeypatch.delenv("MEMORY_PREDICTIVE_FAILURE_LIMIT", raising=False)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Fresh schema from the root migration runner."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    db_path = tmp_path / "src.db"
    c = open_connection(db_path)
    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    title: str,
    text: str,
    outcome_score: float,
    updated_at: str,
    workspace_id: str = "ws",
    status: str = "active",
    valid_to: str | None = None,
) -> None:
    """Insert a decision row with the minimum NOT NULL columns set."""
    conn.execute(
        """INSERT INTO decisions (
            id, workspace_id, title, decision_text, rationale,
            outcome_score, status, valid_from, valid_to,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)""",
        (
            decision_id,
            workspace_id,
            title,
            text,
            outcome_score,
            status,
            updated_at,
            valid_to,
            updated_at,
            updated_at,
        ),
    )
    conn.commit()


def test_returns_empty_when_disabled(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_PREDICTIVE_FAILURE_ENABLED", "false")
    _seed_decision(
        conn,
        decision_id="dec_a",
        title="Use kelly sizing",
        text="quarter kelly",
        outcome_score=-0.7,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    _seed_decision(
        conn,
        decision_id="dec_b",
        title="Use kelly sizing",
        text="quarter kelly",
        outcome_score=0.0,
        updated_at="2026-02-01T00:00:00+00:00",
    )
    out = pf.find_predictive_warnings(conn, workspace_id="ws")
    assert out == []


def test_returns_empty_when_no_decisions(conn: sqlite3.Connection) -> None:
    assert pf.find_predictive_warnings(conn, workspace_id="ws") == []


def test_warns_on_high_overlap_with_failed(conn: sqlite3.Connection) -> None:
    """Fresh decision with same tokens as a failed one → warning."""
    _seed_decision(
        conn,
        decision_id="dec_failure",
        title="Kelly bet sizing for trading drawdown",
        text="Use quarter kelly to limit drawdown on trading positions",
        outcome_score=-0.7,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    _seed_decision(
        conn,
        decision_id="dec_fresh",
        title="Kelly sizing for trading drawdown",
        text="Use quarter kelly to limit drawdown on positions",
        outcome_score=0.0,
        updated_at="2026-03-01T00:00:00+00:00",
    )
    out = pf.find_predictive_warnings(conn, workspace_id="ws")
    assert len(out) == 1
    warn = out[0]
    assert warn.fresh_decision_id == "dec_fresh"
    assert warn.failed_decision_id == "dec_failure"
    assert warn.similarity >= 0.4
    assert "dec_fresh" in warn.reason_text
    assert "dec_failure" in warn.reason_text


def test_does_not_warn_when_overlap_too_low(conn: sqlite3.Connection) -> None:
    """Unrelated topics → no warning even though one failed."""
    _seed_decision(
        conn,
        decision_id="dec_failed_kelly",
        title="Use kelly sizing for trades",
        text="Quarter kelly drawdown control",
        outcome_score=-0.8,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    _seed_decision(
        conn,
        decision_id="dec_fresh_db",
        title="Switch to sqlite WAL mode",
        text="Enable WAL journaling for concurrent reader throughput",
        outcome_score=0.0,
        updated_at="2026-03-01T00:00:00+00:00",
    )
    out = pf.find_predictive_warnings(conn, workspace_id="ws")
    assert out == []


def test_does_not_warn_backward_chronology(conn: sqlite3.Connection) -> None:
    """Failure NEWER than fresh → not informative (we warn forward only)."""
    _seed_decision(
        conn,
        decision_id="dec_old_fresh",
        title="Kelly trading drawdown",
        text="Use quarter kelly limit positions",
        outcome_score=0.0,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    _seed_decision(
        conn,
        decision_id="dec_new_failure",
        title="Kelly trading drawdown",
        text="Use quarter kelly limit positions",
        outcome_score=-0.7,
        updated_at="2026-03-01T00:00:00+00:00",
    )
    out = pf.find_predictive_warnings(conn, workspace_id="ws")
    assert out == []


def test_does_not_warn_on_rated_fresh(conn: sqlite3.Connection) -> None:
    """If fresh has non-trivial outcome_score, scoring already happened."""
    _seed_decision(
        conn,
        decision_id="dec_failure_x",
        title="Kelly trading drawdown",
        text="Quarter kelly positions",
        outcome_score=-0.7,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    # outcome_score=0.5 → already rated positive, no warning needed.
    _seed_decision(
        conn,
        decision_id="dec_rated_fresh",
        title="Kelly trading drawdown approach",
        text="Quarter kelly positions",
        outcome_score=0.5,
        updated_at="2026-03-01T00:00:00+00:00",
    )
    out = pf.find_predictive_warnings(conn, workspace_id="ws")
    assert out == []


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    _seed_decision(
        conn,
        decision_id="dec_failure",
        title="Kelly trading",
        text="Quarter kelly",
        outcome_score=-0.7,
        updated_at="2026-01-01T00:00:00+00:00",
        workspace_id="alpha",
    )
    _seed_decision(
        conn,
        decision_id="dec_fresh",
        title="Kelly trading",
        text="Quarter kelly",
        outcome_score=0.0,
        updated_at="2026-03-01T00:00:00+00:00",
        workspace_id="beta",
    )
    assert pf.find_predictive_warnings(conn, workspace_id="alpha") == []
    assert pf.find_predictive_warnings(conn, workspace_id="beta") == []


def test_picks_best_match_when_multiple_failures(conn: sqlite3.Connection) -> None:
    """When fresh overlaps two failures, picks the highest-similarity."""
    _seed_decision(
        conn,
        decision_id="dec_failure_low",
        title="Kelly position drawdown",
        text="kelly",
        outcome_score=-0.6,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    _seed_decision(
        conn,
        decision_id="dec_failure_high",
        title="Kelly trading sizing drawdown limit",
        text="kelly drawdown sizing trading",
        outcome_score=-0.8,
        updated_at="2026-02-01T00:00:00+00:00",
    )
    _seed_decision(
        conn,
        decision_id="dec_fresh",
        title="Kelly trading sizing drawdown",
        text="kelly trading sizing drawdown",
        outcome_score=0.0,
        updated_at="2026-03-01T00:00:00+00:00",
    )
    out = pf.find_predictive_warnings(conn, workspace_id="ws")
    assert len(out) == 1
    assert out[0].failed_decision_id == "dec_failure_high"


def test_limit_caps_results(conn: sqlite3.Connection) -> None:
    """Default emit_limit caps the result set."""
    # Seed 8 distinct fresh decisions each matching their own failure.
    for i in range(8):
        _seed_decision(
            conn,
            decision_id=f"dec_fail_{i}",
            title=f"Topic{i} sizing trading drawdown",
            text=f"Topic{i} sizing trading drawdown content",
            outcome_score=-0.6,
            updated_at=f"2026-01-{i + 1:02d}T00:00:00+00:00",
        )
        _seed_decision(
            conn,
            decision_id=f"dec_fresh_{i}",
            title=f"Topic{i} sizing trading drawdown approach",
            text=f"Topic{i} sizing trading drawdown content",
            outcome_score=0.0,
            updated_at=f"2026-03-{i + 1:02d}T00:00:00+00:00",
        )
    out = pf.find_predictive_warnings(conn, workspace_id="ws")
    assert len(out) == 5  # default emit_limit
    out_capped = pf.find_predictive_warnings(conn, workspace_id="ws", limit=2)
    assert len(out_capped) == 2


def test_env_helpers_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pf.outcome_threshold() == pytest.approx(-0.3)
    assert pf.jaccard_min() == pytest.approx(0.4)
    assert pf.emit_limit() == 5
    assert pf.is_enabled() is True


def test_env_threshold_override(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stricter outcome threshold filters out borderline failures."""
    monkeypatch.setenv("MEMORY_PREDICTIVE_FAILURE_OUTCOME_THRESHOLD", "-0.9")
    _seed_decision(
        conn,
        decision_id="dec_borderline",
        title="Kelly trading drawdown",
        text="quarter kelly positions",
        outcome_score=-0.5,  # above -0.9 threshold → not a "failure" here
        updated_at="2026-01-01T00:00:00+00:00",
    )
    _seed_decision(
        conn,
        decision_id="dec_fresh",
        title="Kelly trading drawdown",
        text="quarter kelly positions",
        outcome_score=0.0,
        updated_at="2026-03-01T00:00:00+00:00",
    )
    out = pf.find_predictive_warnings(conn, workspace_id="ws")
    assert out == []


# ============================================================
# Vector5-audit-2 coverage — new behaviour from audit round 4
# ============================================================


def test_schema_invariant_forbids_null_updated_at(conn: sqlite3.Connection) -> None:
    """Vector5-audit-2 H1 schema check: ``decisions.updated_at`` is
    NOT NULL by the migrations, so the audit-claimed sentinel-string
    chronology bug can't manifest in production. The defensive
    ``AND updated_at IS NOT NULL`` SQL filter is belt-and-suspenders
    against future migration drift."""
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        conn.execute(
            """INSERT INTO decisions (
                id, workspace_id, title, decision_text, rationale,
                outcome_score, status, valid_from, created_at, updated_at
            ) VALUES (?, 'ws', 'Kelly trading',
                      'kelly text', '', -0.7,
                      'active', ?, ?, ?)""",
            (
                "dec_null_upd",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                None,
            ),
        )


def test_skips_decisions_with_valid_to_in_past(conn: sqlite3.Connection) -> None:
    """Vector5-audit-2 H2: bi-temporal expiry — a decision whose
    ``valid_to`` is in the past should not surface as failure or fresh."""
    _seed_decision(
        conn,
        decision_id="dec_expired_failure",
        title="Kelly trading drawdown sizing",
        text="kelly quarter positions drawdown sizing",
        outcome_score=-0.7,
        updated_at="2026-01-01T00:00:00+00:00",
        valid_to="2026-02-01T00:00:00+00:00",  # expired before today
    )
    _seed_decision(
        conn,
        decision_id="dec_fresh",
        title="Kelly trading drawdown sizing",
        text="kelly quarter positions drawdown sizing",
        outcome_score=0.0,
        updated_at="2026-03-01T00:00:00+00:00",
    )
    out = pf.find_predictive_warnings(conn, workspace_id="ws")
    assert out == []


def test_min_token_set_floor_blocks_short_text(conn: sqlite3.Connection) -> None:
    """Vector5-audit-2 M5: Jaccard noise on 2-3 token sets — guard
    drops pairs where either side has fewer than 4 cleaned tokens."""
    _seed_decision(
        conn,
        decision_id="dec_short_failure",
        title="kelly sizing",
        text="kelly",
        outcome_score=-0.7,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    _seed_decision(
        conn,
        decision_id="dec_short_fresh",
        title="kelly sizing",
        text="kelly",
        outcome_score=0.0,
        updated_at="2026-03-01T00:00:00+00:00",
    )
    out = pf.find_predictive_warnings(conn, workspace_id="ws")
    assert out == []  # below min-token-set floor


def test_workspace_isolation_returns_warning_in_correct_workspace(
    conn: sqlite3.Connection,
) -> None:
    """Vector5-audit-2 M9: the previous workspace_isolation test was
    vacuous — neither workspace had a complete pair. This one seeds
    a complete pair in alpha + an orphan in beta and verifies the
    isolation guard."""
    _seed_decision(
        conn,
        decision_id="dec_alpha_failure",
        title="Kelly trading drawdown sizing approach",
        text="kelly quarter positions drawdown sizing approach",
        outcome_score=-0.7,
        updated_at="2026-01-01T00:00:00+00:00",
        workspace_id="alpha",
    )
    _seed_decision(
        conn,
        decision_id="dec_alpha_fresh",
        title="Kelly trading drawdown sizing approach",
        text="kelly quarter positions drawdown sizing approach",
        outcome_score=0.0,
        updated_at="2026-03-01T00:00:00+00:00",
        workspace_id="alpha",
    )
    _seed_decision(
        conn,
        decision_id="dec_beta_orphan",
        title="WAL journaling mode toggle for concurrent readers",
        text="enable sqlite WAL for concurrent readers throughput",
        outcome_score=0.0,
        updated_at="2026-03-01T00:00:00+00:00",
        workspace_id="beta",
    )
    out_alpha = pf.find_predictive_warnings(conn, workspace_id="alpha")
    out_beta = pf.find_predictive_warnings(conn, workspace_id="beta")
    assert len(out_alpha) == 1
    assert out_alpha[0].fresh_decision_id == "dec_alpha_fresh"
    assert out_beta == []
