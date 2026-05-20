"""v3.1 active-memory dashboard helper: build_active_memory."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.api.routes.active_memory_status import build_active_memory

CANONICAL_INIT = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
CANONICAL_OUTCOME = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_BLINDSPOT_DETECT_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_PREDICTIVE_FAILURE_ENABLED", raising=False)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    c = open_connection(tmp_path / "status.db")
    apply_migrations(c)
    c.executescript(CANONICAL_INIT.read_text(encoding="utf-8"))
    c.executescript(CANONICAL_OUTCOME.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def test_active_memory_empty_workspace(conn: sqlite3.Connection) -> None:
    """Fresh workspace → all counts zero, all flags healthy."""
    out = build_active_memory(conn, workspace_id="ws")
    assert out.open_proposals == 0
    assert out.proposals_available is True
    assert out.proposals_enabled is True
    assert out.blindspots == 0
    assert out.blindspots_enabled is True
    assert out.predictive_warnings == 0
    assert out.predictive_warnings_available is True
    assert out.predictive_warnings_enabled is True
    assert out.persisted_warnings_new == 0


def test_active_memory_respects_disabled_flags(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each ``MEMORY_*_ENABLED=false`` flips the corresponding ``..._enabled``
    field on the snapshot."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_ENABLED", "false")
    monkeypatch.setenv("MEMORY_BLINDSPOT_DETECT_ENABLED", "false")
    monkeypatch.setenv("MEMORY_PREDICTIVE_FAILURE_ENABLED", "false")
    out = build_active_memory(conn, workspace_id="ws")
    assert out.proposals_enabled is False
    assert out.blindspots_enabled is False
    assert out.predictive_warnings_enabled is False
    # Scanners short-circuit when disabled → counts stay zero.
    assert out.open_proposals == 0
    assert out.blindspots == 0
    assert out.predictive_warnings == 0


def test_active_memory_counts_open_proposals(conn: sqlite3.Connection) -> None:
    """A seeded insight in the conf window produces a proposal in the count.

    v3.3: the min-evidence gate (default 3) drops thin-signal insights,
    so seed 3 distinct episodes to clear the threshold."""
    for ep_id in ("ep_a", "ep_b", "ep_c"):
        conn.execute(
            "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
            "VALUES (?, 'ws', 'agent_action', 'fixture', '2026-05-19T00:00:00+00:00')",
            (ep_id,),
        )
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, proposed_action,
            target_type, target_id, source_episode_ids_json, confidence,
            status, tags_json, created_at, updated_at)
           VALUES ('ins_a', 'ws', 'lesson', 'a long body for the proposal',
                   'a long body for the proposal', NULL, NULL, NULL,
                   '["ep_a", "ep_b", "ep_c"]', 0.55, 'candidate', '[]',
                   '2026-05-19T00:00:00+00:00',
                   '2026-05-19T00:00:00+00:00')"""
    )
    conn.commit()
    out = build_active_memory(conn, workspace_id="ws")
    assert out.open_proposals == 1


def test_active_memory_counts_persisted_warnings(conn: sqlite3.Connection) -> None:
    """``persisted_warnings_new`` reflects Vector 5 rows landed by brain_pass."""
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES ('ep_warn', 'ws', 'agent_action', 'fixture', "
        "'2026-05-19T00:00:00+00:00')",
    )
    conn.execute(
        """INSERT INTO memory_candidates (
            id, workspace_id, kind, subject, predicate, object, evidence,
            confidence, importance, trust_level, temporal_json,
            write_targets_json, metadata_json, source_episode_id,
            status, created_at, updated_at
        ) VALUES ('cand_pw_x', 'ws', 'predictive_warning', 'dec_a',
                  'lookalike_of', 'dec_b', 'reason', 0.5, 0.5,
                  'agent_observed', '{}', '[]', '{}', 'ep_warn',
                  'new', '2026-05-19T00:00:00+00:00',
                  '2026-05-19T00:00:00+00:00')"""
    )
    conn.commit()
    out = build_active_memory(conn, workspace_id="ws")
    assert out.persisted_warnings_new == 1


def test_active_memory_persisted_warnings_excludes_promoted(
    conn: sqlite3.Connection,
) -> None:
    """Status='accepted' rows don't count as 'new' for the dashboard."""
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES ('ep_promoted', 'ws', 'agent_action', 'fixture', "
        "'2026-05-19T00:00:00+00:00')",
    )
    conn.execute(
        """INSERT INTO memory_candidates (
            id, workspace_id, kind, subject, predicate, object, evidence,
            confidence, importance, trust_level, temporal_json,
            write_targets_json, metadata_json, source_episode_id,
            status, created_at, updated_at
        ) VALUES ('cand_pw_promoted', 'ws', 'predictive_warning', 'dec_a',
                  'lookalike_of', 'dec_b', 'reason', 0.5, 0.5,
                  'agent_observed', '{}', '[]', '{}', 'ep_promoted',
                  'accepted', '2026-05-19T00:00:00+00:00',
                  '2026-05-19T00:00:00+00:00')"""
    )
    conn.commit()
    out = build_active_memory(conn, workspace_id="ws")
    assert out.persisted_warnings_new == 0


def test_active_memory_workspace_isolation(conn: sqlite3.Connection) -> None:
    """Counts for ws_alpha do not leak Vector 5 rows from ws_beta."""
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES ('ep_iso', 'beta', 'agent_action', 'fixture', "
        "'2026-05-19T00:00:00+00:00')",
    )
    conn.execute(
        """INSERT INTO memory_candidates (
            id, workspace_id, kind, subject, predicate, object, evidence,
            confidence, importance, trust_level, temporal_json,
            write_targets_json, metadata_json, source_episode_id,
            status, created_at, updated_at
        ) VALUES ('cand_pw_iso', 'beta', 'predictive_warning', 'dec_a',
                  'lookalike_of', 'dec_b', 'reason', 0.5, 0.5,
                  'agent_observed', '{}', '[]', '{}', 'ep_iso',
                  'new', '2026-05-19T00:00:00+00:00',
                  '2026-05-19T00:00:00+00:00')"""
    )
    conn.commit()
    out_alpha = build_active_memory(conn, workspace_id="alpha")
    out_beta = build_active_memory(conn, workspace_id="beta")
    assert out_alpha.persisted_warnings_new == 0
    assert out_beta.persisted_warnings_new == 1
