"""Phase 1: brief filters Active decisions by outcome_score >= 0."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.cognition.brief import compose_brief
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


def _seed_decision(conn: sqlite3.Connection, **kwargs: object) -> None:
    defaults: dict[str, object] = {
        "id": "dec_x",
        "workspace_id": "ws",
        "title": "T",
        "decision_text": "body",
        "status": "active",
        "valid_from": iso_now(),
        "created_at": iso_now(),
        "updated_at": iso_now(),
        "outcome_score": 0.0,
        "pinned": 0,
        "gist": "g",
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    qs = ", ".join("?" for _ in defaults)
    conn.execute(f"INSERT INTO decisions ({cols}) VALUES ({qs})", tuple(defaults.values()))
    conn.commit()


def test_negative_outcome_decision_excluded_from_active(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id="dec_good", gist="Good decision", outcome_score=0.4)
    _seed_decision(conn, id="dec_bad", gist="Failed approach", outcome_score=-0.5)
    brief = compose_brief(conn, workspace_id="ws")
    # Split body into sections so we assert exclusion within Active scope.
    active_section, _, watch_section = brief.body_md.partition("## Watch-outs")
    assert "dec_good" in active_section
    # Bad decision must NOT appear in Active decisions section.
    assert "dec_bad" not in active_section
    # But it DOES appear under Watch-outs.
    assert "dec_bad" in watch_section
    assert "Failed approach" in watch_section


def test_pinned_negative_outcome_still_appears_in_active(conn: sqlite3.Connection) -> None:
    """Operator-pinned rules survive a negative outcome; they sort beneath
    positive peers but stay visible (operator override)."""
    _seed_decision(
        conn, id="dec_pinned_bad", gist="Pinned but failing", pinned=1, outcome_score=-0.5
    )
    _seed_decision(conn, id="dec_neutral", gist="Neutral active", outcome_score=0.0)
    brief = compose_brief(conn, workspace_id="ws")
    assert "dec_pinned_bad" in brief.body_md
    assert "dec_neutral" in brief.body_md


def test_active_decisions_sorted_by_outcome(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id="dec_meh", gist="meh", outcome_score=0.1)
    _seed_decision(conn, id="dec_great", gist="great", outcome_score=0.8)
    _seed_decision(conn, id="dec_ok", gist="ok", outcome_score=0.4)
    brief = compose_brief(conn, workspace_id="ws")
    # great should appear before meh in the brief body
    idx_great = brief.body_md.find("dec_great")
    idx_meh = brief.body_md.find("dec_meh")
    assert 0 <= idx_great < idx_meh


def test_watch_outs_empty_when_all_positive(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id="dec_g", gist="all good", outcome_score=0.6)
    brief = compose_brief(conn, workspace_id="ws")
    # Watch-outs header should not appear when there are no negatives.
    assert "## Watch-outs" not in brief.body_md


def test_archived_negative_outcome_decision_not_in_watch_outs(
    conn: sqlite3.Connection,
) -> None:
    """v3.5: archived rows already got dispositioned by the operator —
    re-surfacing them in the watch-outs section eats budget and pushes
    real watch-outs out of the limit-N pool. The agent-memory-lite
    workspace had three "v3 smoke" archived rows stuck at the top of
    watch-outs for ~3 days before this filter shipped."""
    _seed_decision(
        conn,
        id="dec_smoke_old",
        gist="archived smoke test",
        outcome_score=-1.0,
        status="archived",
    )
    _seed_decision(
        conn,
        id="dec_real_concern",
        gist="real negative outcome",
        outcome_score=-0.5,
    )
    brief = compose_brief(conn, workspace_id="ws")
    _, _, watch_section = brief.body_md.partition("## Watch-outs")
    # Archived row must NOT surface
    assert "dec_smoke_old" not in watch_section
    # Active negative row STILL must surface
    assert "dec_real_concern" in watch_section


def test_rejected_negative_outcome_decision_not_in_watch_outs(
    conn: sqlite3.Connection,
) -> None:
    """A rejected decision is operator-dispositioned exactly like an archived one
    (the v3.5 rationale applies verbatim). Now that outcome_recompute pins a
    rejected decision negative, the watch-outs guard must exclude it the same
    way -- otherwise the fix would float every rejected decision into watch-outs
    as noise. An active negative row still surfaces."""
    _seed_decision(
        conn,
        id="dec_rej_old",
        gist="rejected idea",
        outcome_score=-0.6,
        status="rejected",
    )
    _seed_decision(
        conn,
        id="dec_real_concern",
        gist="real negative outcome",
        outcome_score=-0.5,
    )
    brief = compose_brief(conn, workspace_id="ws")
    _, _, watch_section = brief.body_md.partition("## Watch-outs")
    # Rejected (dispositioned) row must NOT surface
    assert "dec_rej_old" not in watch_section
    # Active negative row STILL must surface
    assert "dec_real_concern" in watch_section


def test_superseded_negative_outcome_decision_not_in_watch_outs(
    conn: sqlite3.Connection,
) -> None:
    """A superseded decision was refined/replaced, not failed -- its negative
    outcome is purely the SUPERSEDED_PENALTY. It must NOT surface as a 'failed
    approach' watch-out (the agent should be steered to its active successor)."""
    _seed_decision(
        conn, id="dec_super_old", gist="superseded predecessor",
        outcome_score=-0.5, status="superseded",
    )
    _seed_decision(conn, id="dec_real_concern", gist="active failing", outcome_score=-0.4)
    brief = compose_brief(conn, workspace_id="ws")
    _, _, watch_section = brief.body_md.partition("## Watch-outs")
    assert "dec_super_old" not in watch_section  # superseded predecessor filtered
    assert "dec_real_concern" in watch_section  # active negative still surfaces


def test_padded_superseded_decision_not_in_watch_outs(conn: sqlite3.Connection) -> None:
    """The watch-outs terminal-status exclusion is whitespace-stripped
    (LOWER(TRIM(...))), symmetric with the associates filter -- a padded
    superseded status must not leak as a 'failed approach'."""
    _seed_decision(
        conn, id="dec_pad_super", gist="padded superseded",
        outcome_score=-0.5, status="  superseded  ",
    )
    _seed_decision(conn, id="dec_real_concern", gist="active failing", outcome_score=-0.4)
    brief = compose_brief(conn, workspace_id="ws")
    _, _, watch_section = brief.body_md.partition("## Watch-outs")
    assert "dec_pad_super" not in watch_section
    assert "dec_real_concern" in watch_section


def test_terminal_theory_not_in_watch_outs(conn: sqlite3.Connection) -> None:
    """rejected/weakened theories are pinned negative by status penalty, not
    failure feedback; they must not surface as watch-outs. An active negative
    theory still does."""
    for id_, status in (("th_rej", "rejected"), ("th_weak", "weakened"), ("th_live", "supported")):
        conn.execute(
            "INSERT INTO theories (id, workspace_id, title, claim, gist, status, "
            "created_at, updated_at, outcome_score) "
            f"VALUES (?, 'ws', 't', 'c', 'g {id_}', '{status}', ?, ?, -0.5)",
            (id_, iso_now(), iso_now()),
        )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws")
    _, _, watch_section = brief.body_md.partition("## Watch-outs")
    assert "th_rej" not in watch_section  # rejected theory filtered
    assert "th_weak" not in watch_section  # weakened theory filtered
    assert "th_live" in watch_section  # active (supported) negative still surfaces


# ----- round-2 audit: brief cache must be sensitive to outcome_score -----


def test_fingerprint_flips_on_outcome_score_only_change(conn: sqlite3.Connection) -> None:
    """outcome_recompute rewrites outcome_score WITHOUT bumping updated_at, so a
    fingerprint keyed only on MAX(updated_at) would not flip and the brief cache
    would serve a stale body. The fingerprint must fold outcome_score in."""
    from agent_memory_lite.cognition.brief_cache import _workspace_fingerprint  # noqa: PLC0415

    _seed_decision(conn, id="dec_x", gist="g", outcome_score=0.5)
    fp_before = _workspace_fingerprint(conn, "ws")
    # Mirror outcome_recompute exactly: outcome_score only, updated_at untouched.
    conn.execute("UPDATE decisions SET outcome_score = -0.9 WHERE id = 'dec_x'")
    conn.commit()
    fp_after = _workspace_fingerprint(conn, "ws")
    assert fp_before != fp_after


def test_fingerprint_flips_on_theory_outcome_change(conn: sqlite3.Connection) -> None:
    """Theories carry outcome_score and feed the brief (associates + watch-outs),
    and outcome_recompute rewrites it without bumping updated_at -- so the
    fingerprint must fold theories' outcome in too, not just decisions/behaviors."""
    from agent_memory_lite.cognition.brief_cache import _workspace_fingerprint  # noqa: PLC0415

    conn.execute(
        "INSERT INTO theories (id, workspace_id, title, claim, gist, status, "
        "created_at, updated_at, outcome_score) "
        "VALUES ('th_o', 'ws', 't', 'c', 'g', 'active', ?, ?, 0.5)",
        (iso_now(), iso_now()),
    )
    conn.commit()
    fp_before = _workspace_fingerprint(conn, "ws")
    conn.execute("UPDATE theories SET outcome_score = -0.9 WHERE id = 'th_o'")  # no updated_at
    conn.commit()
    assert _workspace_fingerprint(conn, "ws") != fp_before


def test_fingerprint_flips_on_concept_deactivation(conn: sqlite3.Connection) -> None:
    """concepts/skills/issues surface in the brief but were absent from the
    fingerprint; a deactivation (active=0, bumps updated_at) must invalidate the
    cached brief. Representative of the concepts/skills/issues fingerprint add."""
    from agent_memory_lite.cognition.brief_cache import _workspace_fingerprint  # noqa: PLC0415

    conn.execute(
        "INSERT INTO concepts (id, workspace_id, name, kind, definition, "
        "definition_one_line, aliases_json, active, created_at, updated_at) "
        "VALUES ('con_x', 'ws', 'c', 'term', 'd', 'd1', '[]', 1, ?, ?)",
        (iso_now(), iso_now()),
    )
    conn.commit()
    fp_before = _workspace_fingerprint(conn, "ws")
    conn.execute(
        "UPDATE concepts SET active = 0, updated_at = '2099-01-01T00:00:00Z' WHERE id = 'con_x'"
    )
    conn.commit()
    assert _workspace_fingerprint(conn, "ws") != fp_before


def test_fingerprint_flips_on_theory_status_change(conn: sqlite3.Connection) -> None:
    """A theory status flip bumps updated_at; theories must be in the fingerprint
    at all so that flip invalidates the cached brief (theories were previously
    absent from the fingerprint entirely)."""
    from agent_memory_lite.cognition.brief_cache import _workspace_fingerprint  # noqa: PLC0415

    conn.execute(
        "INSERT INTO theories (id, workspace_id, title, claim, gist, status, "
        "created_at, updated_at, outcome_score) "
        "VALUES ('th_s', 'ws', 't', 'c', 'g', 'active', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 0.5)"
    )
    conn.commit()
    fp_before = _workspace_fingerprint(conn, "ws")
    conn.execute(
        "UPDATE theories SET status = 'rejected', updated_at = '2026-06-01T00:00:00Z' "
        "WHERE id = 'th_s'"
    )
    conn.commit()
    assert _workspace_fingerprint(conn, "ws") != fp_before


def test_cache_invalidates_when_outcome_score_recomputed(conn: sqlite3.Connection) -> None:
    """End-to-end: an outcome-only discredit must NOT serve a stale cached brief
    that still lists the row as a positive Active decision (the exact self-
    contradiction the discredited-row filters set out to prevent)."""
    _seed_decision(conn, id="dec_flip", gist="was good now failing", outcome_score=0.5)
    first = compose_brief(conn, workspace_id="ws", max_tokens=500)
    assert first.cache_hit is False
    assert "dec_flip" in first.body_md.partition("## Watch-outs")[0]  # in Active
    # Discredit via an outcome-only write (no updated_at change), as the brain
    # pass does -- the cache must still invalidate.
    conn.execute("UPDATE decisions SET outcome_score = -0.9 WHERE id = 'dec_flip'")
    conn.commit()
    second = compose_brief(conn, workspace_id="ws", max_tokens=500)
    assert second.cache_hit is False  # fingerprint flipped -> fresh build
    active2, _, watch2 = second.body_md.partition("## Watch-outs")
    assert "dec_flip" not in active2  # no longer a positive Active decision
    assert "dec_flip" in watch2  # now correctly a watch-out
