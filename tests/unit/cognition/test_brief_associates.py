"""Phase 2: brief surfaces Hebbian associates of active decisions."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.cognition.brief import compose_brief
from agent_memory_lite.repositories.soft_edges_repo import upsert_soft_edge
from agent_memory_lite.utils.time import iso_now


@pytest.fixture(autouse=True)
def _isolate_brief_cache() -> Iterator[None]:
    from agent_memory_lite.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    yield
    brief_mod._BRIEF_CACHE.clear()


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


def _seed_decision(conn: sqlite3.Connection, *, id_: str, outcome: float = 0.5) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, 'ws', 't', 'b', ?, 'active', ?, ?, ?, ?, 0)""",
        (id_, f"gist for {id_}", iso_now(), iso_now(), iso_now(), outcome),
    )
    conn.commit()


def _seed_theory(conn: sqlite3.Connection, *, id_: str, outcome: float = 0.5) -> None:
    conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, claim, gist, status, created_at, updated_at,
            outcome_score)
           VALUES (?, 'ws', 't', 'c', ?, 'active', ?, ?, ?)""",
        (id_, f"gist for {id_}", iso_now(), iso_now(), outcome),
    )
    conn.commit()


def _seed_behavior(
    conn: sqlite3.Connection, *, id_: str, active: int = 1, outcome: float = 0.0, pinned: int = 0
) -> None:
    conn.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, scope, priority, rule, rule_one_line,
            applies_to_json, active, pinned, created_at, updated_at, outcome_score)
           VALUES (?, 'ws', ?, 'operating_rule', 'workspace', 'project_convention',
                   'rule body', ?, '[]', ?, ?, ?, ?, ?)""",
        (id_, id_, f"rule for {id_}", active, pinned, iso_now(), iso_now(), outcome),
    )
    conn.commit()


def test_associates_section_appears_when_edges_exist(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_seed", outcome=0.6)
    _seed_theory(conn, id_="th_assoc", outcome=0.3)
    upsert_soft_edge(
        conn,
        workspace_id="ws",
        src="decision:dec_seed",
        dst="theory:th_assoc",
        kind="co_retrieved",
        weight_increment=2.0,  # well above min_edge_weight=0.1
    )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Associated to current decisions" in brief.body_md
    assert "theory:th_assoc" in brief.body_md


def test_associates_section_absent_with_no_edges(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_alone", outcome=0.6)
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Associated to current decisions" not in brief.body_md


def test_associates_section_absent_when_no_decisions(conn: sqlite3.Connection) -> None:
    """No seeds → no associates section."""
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Associated to current decisions" not in brief.body_md


def test_superseded_neighbor_filtered_from_associates(conn: sqlite3.Connection) -> None:
    """A superseded neighbor must NOT be surfaced as a positive association (the
    same brief lists it as a watch-out -- surfacing both is self-contradiction).
    A healthy neighbor on the same seed still surfaces."""
    _seed_decision(conn, id_="dec_seed", outcome=0.6)
    _seed_theory(conn, id_="th_good", outcome=0.4)
    _seed_theory(conn, id_="th_dead", outcome=0.5)
    conn.execute("UPDATE theories SET status = 'superseded' WHERE id = 'th_dead'")
    conn.commit()
    for dst in ("theory:th_good", "theory:th_dead"):
        upsert_soft_edge(
            conn,
            workspace_id="ws",
            src="decision:dec_seed",
            dst=dst,
            kind="co_retrieved",
            weight_increment=2.0,
        )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Associated to current decisions" in brief.body_md
    assert "theory:th_good" in brief.body_md  # healthy neighbor surfaces
    assert "theory:th_dead" not in brief.body_md  # superseded neighbor filtered


def test_rejected_neighbor_filtered_from_associates(conn: sqlite3.Connection) -> None:
    """'rejected' is terminal. A rejected neighbor with a NON-negative outcome
    (so the outcome arm does not catch it) must still be filtered by status --
    the status guard formerly missed 'rejected', leaking it as a positive
    association. A healthy neighbor on the same seed still surfaces."""
    _seed_decision(conn, id_="dec_seed", outcome=0.6)
    _seed_theory(conn, id_="th_good", outcome=0.4)
    _seed_theory(conn, id_="th_rej", outcome=0.5)  # non-negative on purpose
    conn.execute("UPDATE theories SET status = 'rejected' WHERE id = 'th_rej'")
    conn.commit()
    for dst in ("theory:th_good", "theory:th_rej"):
        upsert_soft_edge(
            conn,
            workspace_id="ws",
            src="decision:dec_seed",
            dst=dst,
            kind="co_retrieved",
            weight_increment=2.0,
        )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Associated to current decisions" in brief.body_md
    assert "theory:th_good" in brief.body_md  # healthy neighbor surfaces
    assert "theory:th_rej (assoc=" not in brief.body_md  # rejected filtered


def test_weakened_theory_neighbor_filtered_from_associates(conn: sqlite3.Connection) -> None:
    """'weakened' is the terminal theory status. A freshly-weakened theory whose
    outcome_score has NOT yet been recomputed negative (stale non-negative, the
    pre-brain-pass window) must still be filtered by the status arm -- otherwise
    it leaks as a positive associate. A healthy neighbor still surfaces."""
    _seed_decision(conn, id_="dec_seed", outcome=0.6)
    _seed_theory(conn, id_="th_good", outcome=0.4)
    _seed_theory(conn, id_="th_weak", outcome=0.45)  # stale non-negative on purpose
    conn.execute("UPDATE theories SET status = 'weakened' WHERE id = 'th_weak'")
    conn.commit()
    for dst in ("theory:th_good", "theory:th_weak"):
        upsert_soft_edge(
            conn,
            workspace_id="ws",
            src="decision:dec_seed",
            dst=dst,
            kind="co_retrieved",
            weight_increment=2.0,
        )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Associated to current decisions" in brief.body_md
    assert "theory:th_good" in brief.body_md  # healthy neighbor surfaces
    assert "theory:th_weak (assoc=" not in brief.body_md  # weakened filtered


def test_deactivated_behavior_neighbor_filtered_from_associates(
    conn: sqlite3.Connection,
) -> None:
    """A deactivated behavior (active=0) is the behavior-kind terminal state --
    behaviors have no 'status' column. With its DEFAULT 0.0 outcome_score (the
    realistic auto-archive case) the outcome arm misses it, so the active arm
    must filter it -- otherwise a dead rule surfaces as a live association."""
    _seed_decision(conn, id_="dec_seed", outcome=0.6)
    _seed_theory(conn, id_="th_good", outcome=0.4)
    _seed_behavior(conn, id_="beh_off", active=0, outcome=0.0)  # deactivated, non-negative
    for dst in ("theory:th_good", "behavior:beh_off"):
        upsert_soft_edge(
            conn,
            workspace_id="ws",
            src="decision:dec_seed",
            dst=dst,
            kind="co_retrieved",
            weight_increment=2.0,
        )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Associated to current decisions" in brief.body_md
    assert "theory:th_good" in brief.body_md  # healthy neighbor surfaces
    assert "behavior:beh_off (assoc=" not in brief.body_md  # deactivated filtered


def test_deactivated_concept_and_skill_neighbors_filtered_from_associates(
    conn: sqlite3.Connection,
) -> None:
    """Concepts and skills are the other two active-flag kinds (memory_archive
    soft-archives behaviors/skills/concepts to active=0; none have a status
    column). A deactivated concept/skill neighbor must be filtered, not surfaced
    as a positive association -- the sibling case to the behavior fix."""
    _seed_decision(conn, id_="dec_seed", outcome=0.6)
    _seed_theory(conn, id_="th_good", outcome=0.4)
    conn.execute(
        """INSERT INTO concepts
           (id, workspace_id, name, kind, definition, definition_one_line,
            aliases_json, active, created_at, updated_at)
           VALUES ('con_off', 'ws', 'dead concept', 'term', 'd', 'd1', '[]', 0, ?, ?)""",
        (iso_now(), iso_now()),
    )
    conn.execute(
        """INSERT INTO skills
           (id, workspace_id, name, subtype, summary, when_to_use_json, inputs_json,
            outputs_json, tools_json, related_roles_json, source_episode_id,
            confidence, active, created_at, updated_at)
           VALUES ('skl_off', 'ws', 'dead skill', 'skill', 's', '[]', '[]', '[]',
                   '[]', '[]', NULL, 0.7, 0, ?, ?)""",
        (iso_now(), iso_now()),
    )
    conn.commit()
    for dst in ("theory:th_good", "concept:con_off", "skill:skl_off"):
        upsert_soft_edge(
            conn,
            workspace_id="ws",
            src="decision:dec_seed",
            dst=dst,
            kind="co_retrieved",
            weight_increment=2.0,
        )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Associated to current decisions" in brief.body_md
    assert "theory:th_good" in brief.body_md  # healthy neighbor surfaces
    assert "concept:con_off (assoc=" not in brief.body_md  # deactivated concept filtered
    assert "skill:skl_off (assoc=" not in brief.body_md  # deactivated skill filtered


def test_is_discredited_covers_every_terminal_mechanism() -> None:
    """_is_discredited must classify the dead state of EVERY kind reachable as an
    associate neighbor, by its mechanism: status denylist (decision/theory/
    insight), active=0 (behavior/skill/concept), is_archived (episode/chunk),
    live-status allowlist (task/plan_step/issue). Live rows must pass."""
    from agent_memory_lite.cognition.brief_sections_organ import _is_discredited  # noqa: PLC0415

    dead: list[dict[str, object]] = [
        {"kind": "decision", "status": "superseded"},
        {"kind": "theory", "status": "weakened"},
        {"kind": "insight", "status": "rejected"},
        {"kind": "behavior", "active": False},
        {"kind": "concept", "active": False},
        {"kind": "skill", "active": False},
        {"kind": "episode", "is_archived": True},
        {"kind": "chunk", "is_archived": True},
        {"kind": "task", "status": "done"},
        {"kind": "task", "status": "cancelled"},  # task open-vocab terminal denylist
        {"kind": "plan_step", "status": "skipped"},
        {"kind": "issue", "status": "fixed"},
        {"kind": "decision", "status": "Archived"},  # case-folded
        {"kind": "decision", "status": "  rejected  "},  # whitespace-stripped
        {"kind": "decision", "status": "archived", "pinned": True},  # terminal beats pin
    ]
    for proj in dead:
        assert _is_discredited(proj), f"expected discredited: {proj}"
    live: list[dict[str, object]] = [
        {"kind": "decision", "status": "active", "outcome_score": 0.5},
        {"kind": "theory", "status": "supported", "outcome_score": 0.2},
        {"kind": "task", "status": "in_progress"},
        {"kind": "task", "status": "blocked"},  # open-vocab live state (audit 2026-06-26)
        {"kind": "task", "status": "pending"},  # open-vocab live state, not terminal
        {"kind": "plan_step", "status": "blocked"},  # blocked is a LIVE obstacle
        {"kind": "plan_step", "status": "active"},
        {"kind": "issue", "status": "open"},
        {"kind": "behavior", "active": True, "outcome_score": 0.0},
        {"kind": "episode", "is_archived": False},
        {"kind": "task", "status": "  in_progress  "},  # padded live not over-filtered
    ]
    for proj in live:
        assert not _is_discredited(proj), f"expected live: {proj}"


def test_is_discredited_outcome_arm_is_opt_out() -> None:
    """include_outcome_arm=False (used by recall, which owns its own outcome_floor)
    must suppress ONLY the bare negative-outcome arm -- the structural terminal /
    deactivated / archived / work-item arms still fire. A live non-pinned row with a
    negative outcome_score is discredited by default but NOT when the arm is opt-out;
    a structurally-terminal row is dropped either way."""
    from agent_memory_lite.cognition.brief_sections_organ import _is_discredited  # noqa: PLC0415

    live_negative = {"kind": "decision", "status": "active", "outcome_score": -0.3}
    assert _is_discredited(live_negative) is True  # default: outcome arm fires
    assert _is_discredited(live_negative, include_outcome_arm=False) is False

    # Structural arms are independent of the flag.
    for terminal in (
        {"kind": "decision", "status": "superseded", "outcome_score": -0.3},
        {"kind": "behavior", "active": False, "outcome_score": -0.3},
        {"kind": "episode", "is_archived": True, "outcome_score": -0.3},
        {"kind": "task", "status": "done", "outcome_score": -0.3},
    ):
        assert _is_discredited(terminal, include_outcome_arm=False) is True, terminal
    # A live POSITIVE row stays live under both.
    live_positive = {"kind": "decision", "status": "active", "outcome_score": 0.4}
    assert _is_discredited(live_positive, include_outcome_arm=False) is False


def test_terminal_workitem_and_archived_episode_neighbors_filtered(
    conn: sqlite3.Connection,
) -> None:
    """End-to-end: a done task (work-item live-status allowlist) and an archived
    episode (is_archived) reached as associates must be filtered. Proves the
    projection -> _is_discredited -> brief chain for the two newest mechanisms."""
    _seed_decision(conn, id_="dec_seed", outcome=0.6)
    _seed_theory(conn, id_="th_good", outcome=0.4)
    conn.execute(
        "INSERT INTO tasks (id, workspace_id, task_id, goal, goal_one_line, status, "
        "next_action, updated_at) VALUES ('task_done', 'ws', 't1', 'g', 'goal one', "
        "'done', 'n', ?)",
        (iso_now(),),
    )
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at, "
        "is_archived) VALUES ('ep_arch', 'ws', 'agent_action', 'raw text', ?, 1)",
        (iso_now(),),
    )
    conn.commit()
    for dst in ("theory:th_good", "task:task_done", "episode:ep_arch"):
        upsert_soft_edge(
            conn,
            workspace_id="ws",
            src="decision:dec_seed",
            dst=dst,
            kind="co_retrieved",
            weight_increment=2.0,
        )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Associated to current decisions" in brief.body_md
    assert "theory:th_good" in brief.body_md  # healthy neighbor surfaces
    assert "task:task_done (assoc=" not in brief.body_md  # done task filtered
    assert "episode:ep_arch (assoc=" not in brief.body_md  # archived episode filtered


def test_negative_outcome_neighbor_filtered_from_associates(conn: sqlite3.Connection) -> None:
    """A non-pinned neighbor with a negative outcome_score is dead signal."""
    _seed_decision(conn, id_="dec_seed", outcome=0.6)
    _seed_theory(conn, id_="th_neg", outcome=-0.5)  # negative, not pinned
    upsert_soft_edge(
        conn,
        workspace_id="ws",
        src="decision:dec_seed",
        dst="theory:th_neg",
        kind="co_retrieved",
        weight_increment=2.0,
    )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws")
    # Filtered from ASSOCIATES (it legitimately still appears as a watch-out,
    # rendered as "(outcome=...)" not "(assoc=...)").
    assert "theory:th_neg (assoc=" not in brief.body_md
