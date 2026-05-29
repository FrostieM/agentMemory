"""Maturity -> confidence recompute (release task #121).

Pins that observed outcomes actually move stored capability confidence: a
track record of success raises it, failure lowers it, idle time decays it back
toward the operator base, and a second identical pass is a no-op.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.maintenance.capability_confidence import (
    recompute_capability_confidence,
)
from agent_memory_lite.repositories.capabilities_playbooks_repo import upsert_playbook_row
from agent_memory_lite.repositories.capabilities_roles_repo import upsert_role_row
from agent_memory_lite.repositories.capabilities_skills_repo import upsert_skill_row

_NOW = datetime(2026, 5, 29, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "m.db"))
    c.row_factory = sqlite3.Row
    apply_migrations(c, MIGRATION_DIR)
    yield c
    c.close()


def _seed(
    conn: sqlite3.Connection,
    *,
    sid: str,
    subtype: str = "skill",
    confidence: float,
    base: float | None = None,
    usage: int = 0,
    success: int = 0,
    failure: int = 0,
    last_invoked_at: str | None = None,
    active: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO skills (id, workspace_id, name, subtype, summary, body_md,
            confidence, base_confidence, active, usage_count, success_count,
            failure_count, last_invoked_at, created_at, updated_at)
        VALUES (?, 'w', ?, ?, 'sum', '', ?, ?, ?, ?, ?, ?, ?,
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        """,
        (sid, sid, subtype, confidence, base, active, usage, success, failure, last_invoked_at),
    )


def _confidence(conn: sqlite3.Connection, sid: str) -> tuple[float, float | None]:
    row = conn.execute(
        "SELECT confidence, base_confidence FROM skills WHERE id = ?", (sid,)
    ).fetchone()
    return float(row["confidence"]), (
        float(row["base_confidence"]) if row["base_confidence"] is not None else None
    )


def test_success_track_record_raises_confidence_and_captures_base(conn: sqlite3.Connection) -> None:
    # base NULL -> captured from the pristine operator confidence (0.5).
    _seed(conn, sid="s1", confidence=0.5, base=None, usage=20, success=18, failure=2)
    stats = recompute_capability_confidence(conn, workspace_id="w", half_life_days=30.0, now=_NOW)
    assert stats.scanned == 1
    assert stats.updated == 1
    conf, base = _confidence(conn, "s1")
    assert base == 0.5  # anchor captured
    assert conf > 0.5  # 18/20 success pulls confidence up
    assert conf == pytest.approx(0.82, abs=0.02)


def test_failure_track_record_lowers_confidence(conn: sqlite3.Connection) -> None:
    _seed(conn, sid="s1", confidence=0.8, base=0.8, usage=20, success=2, failure=18)
    recompute_capability_confidence(conn, workspace_id="w", half_life_days=30.0, now=_NOW)
    conf, base = _confidence(conn, "s1")
    assert base == 0.8
    assert conf < 0.8  # mostly-failing capability loses trust


def test_idle_decay_pulls_confidence_back_toward_base(conn: sqlite3.Connection) -> None:
    # Same strong success record, but last used 90 days ago (3 half-lives).
    stale = (_NOW - timedelta(days=90)).isoformat()
    _seed(
        conn,
        sid="fresh",
        confidence=0.5,
        base=0.5,
        usage=20,
        success=18,
        failure=2,
        last_invoked_at=_NOW.isoformat(),
    )
    _seed(
        conn,
        sid="idle",
        confidence=0.5,
        base=0.5,
        usage=20,
        success=18,
        failure=2,
        last_invoked_at=stale,
    )
    recompute_capability_confidence(conn, workspace_id="w", half_life_days=30.0, now=_NOW)
    fresh_conf, _ = _confidence(conn, "fresh")
    idle_conf, _ = _confidence(conn, "idle")
    # Idle decays the adjustment, so its confidence sits closer to base (0.5).
    assert idle_conf < fresh_conf
    assert abs(idle_conf - 0.5) < abs(fresh_conf - 0.5)


def test_second_identical_pass_is_a_noop(conn: sqlite3.Connection) -> None:
    _seed(conn, sid="s1", confidence=0.5, base=None, usage=20, success=18, failure=2)
    first = recompute_capability_confidence(conn, workspace_id="w", half_life_days=30.0, now=_NOW)
    second = recompute_capability_confidence(conn, workspace_id="w", half_life_days=30.0, now=_NOW)
    assert first.updated == 1
    assert second.updated == 0  # idempotent: counters + age unchanged -> no write


def test_usage_without_outcomes_stays_at_base(conn: sqlite3.Connection) -> None:
    # Invoked many times but with NO recorded success/failure: invocations are
    # not evidence about success, so confidence must stay at the operator base
    # and must NOT drift toward the curve's neutral 0.5 prior.
    _seed(conn, sid="s1", confidence=0.85, base=0.85, usage=50, success=0, failure=0)
    stats = recompute_capability_confidence(conn, workspace_id="w", half_life_days=30.0, now=_NOW)
    conf, base = _confidence(conn, "s1")
    assert base == 0.85
    assert conf == pytest.approx(0.85)
    assert stats.updated == 0  # nothing moved


def test_upsert_anchors_and_reanchors_base_confidence(conn: sqlite3.Connection) -> None:
    # base_confidence is captured at the upsert path, not lazily, so an operator
    # re-upsert with a corrected confidence re-anchors the maturity curve
    # instead of having the next brain pass discard the new value.
    kw: dict[str, object] = {
        "when_to_use": [],
        "inputs": [],
        "outputs": [],
        "tools": [],
        "related_roles": [],
        "source_episode_id": None,
        "active": True,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    upsert_skill_row(
        conn,
        skill_id="sk1",
        workspace_id="w",
        name="n",
        summary="s",
        confidence=0.7,
        updated_at="2026-01-01T00:00:00+00:00",
        **kw,
    )
    row = conn.execute("SELECT confidence, base_confidence FROM skills WHERE id = 'sk1'").fetchone()
    assert row["base_confidence"] == 0.7  # anchored at insert

    upsert_skill_row(
        conn,
        skill_id="sk1",
        workspace_id="w",
        name="n",
        summary="s2",
        confidence=0.95,
        updated_at="2026-02-01T00:00:00+00:00",
        **kw,
    )
    row2 = conn.execute(
        "SELECT confidence, base_confidence FROM skills WHERE id = 'sk1'"
    ).fetchone()
    assert row2["confidence"] == 0.95
    assert row2["base_confidence"] == 0.95  # re-anchored, not stuck at 0.7


def test_role_and_playbook_upserts_reanchor_base_confidence(conn: sqlite3.Connection) -> None:
    # The role/playbook upsert SQL mirrors the skill one; pin their ON CONFLICT
    # base_confidence re-anchor too so a future edit to either repo can't regress.
    common: dict[str, object] = {
        "source_episode_id": None,
        "active": True,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    for conf, when in ((0.6, "2026-01-01T00:00:00+00:00"), (0.9, "2026-02-01T00:00:00+00:00")):
        upsert_role_row(
            conn,
            role_id="ro",
            workspace_id="w",
            name="n",
            purpose="p",
            responsibilities=[],
            boundaries=[],
            handoff_triggers=[],
            tools=[],
            confidence=conf,
            updated_at=when,
            **common,
        )
        upsert_playbook_row(
            conn,
            playbook_id="pb",
            workspace_id="w",
            name="n",
            goal="g",
            triggers=[],
            steps=[],
            success_criteria=[],
            required_skills=[],
            confidence=conf,
            updated_at=when,
            **common,
        )
    rows = conn.execute("SELECT subtype, base_confidence FROM skills ORDER BY subtype").fetchall()
    anchored = {r["subtype"]: r["base_confidence"] for r in rows}
    assert anchored == {"playbook": 0.9, "role": 0.9}  # re-anchored to the new value


def test_skips_inactive_and_respects_limit(conn: sqlite3.Connection) -> None:
    _seed(conn, sid="a", confidence=0.5, usage=10, success=9, failure=1)
    _seed(conn, sid="b", confidence=0.5, usage=10, success=9, failure=1)
    _seed(conn, sid="dead", confidence=0.5, usage=10, success=9, failure=1, active=0)
    stats = recompute_capability_confidence(
        conn, workspace_id="w", half_life_days=30.0, now=_NOW, limit=1
    )
    assert stats.scanned == 1  # bounded by limit
    # the inactive row is never eligible regardless of limit
    stats2 = recompute_capability_confidence(
        conn, workspace_id="w", half_life_days=30.0, now=_NOW, limit=100
    )
    assert stats2.scanned == 2  # 'a' and 'b' only; 'dead' excluded
