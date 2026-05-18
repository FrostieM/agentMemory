"""Unit tests for v3 memory_brief composer."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.v3.cognition.brief import (
    DEFAULT_TOKEN_BUDGET,
    approx_tokens,
    compose_brief,
    fetch_skill_body,
    fit_to_budget,
)

SCHEMA_V3_PATH = Path(__file__).resolve().parents[3] / "migrations" / "v3" / "0001_init.sql"


@pytest.fixture(autouse=True)
def _isolate_brief_cache() -> Iterator[None]:
    """Module-level brief cache must not leak between tests."""
    from agent_memory_lite.v3.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    try:
        yield
    finally:
        brief_mod._BRIEF_CACHE.clear()


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_V3_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_decision(conn: sqlite3.Connection, **kwargs) -> None:
    defaults = {
        "id": "dec_x",
        "workspace_id": "ws",
        "title": "T",
        "decision_text": "body",
        "gist": "Gist line",
        "status": "active",
        "pinned": 0,
        "valid_from": "2026-05-17T00:00:00Z",
        "created_at": "2026-05-17T00:00:00Z",
        "updated_at": "2026-05-17T00:00:00Z",
    }
    defaults.update(kwargs)
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, pinned,
            valid_from, created_at, updated_at)
           VALUES (:id, :workspace_id, :title, :decision_text, :gist, :status,
                   :pinned, :valid_from, :created_at, :updated_at)""",
        defaults,
    )
    conn.commit()


def _seed_behavior(conn: sqlite3.Connection, **kwargs) -> None:
    defaults = {
        "id": "beh_x",
        "workspace_id": "ws",
        "name": "rule-name",
        "kind": "operating_rule",
        "rule": "body",
        "rule_one_line": "One-line rule",
        "pinned": 1,
        "active": 1,
        "created_at": "ts",
        "updated_at": "ts",
        "applies_to_json": "[]",
    }
    defaults.update(kwargs)
    conn.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, rule, rule_one_line, pinned, active,
            applies_to_json, created_at, updated_at)
           VALUES (:id, :workspace_id, :name, :kind, :rule, :rule_one_line,
                   :pinned, :active, :applies_to_json, :created_at, :updated_at)""",
        defaults,
    )
    conn.commit()


def _seed_task(conn: sqlite3.Connection, **kwargs) -> None:
    defaults = {
        "id": "task_x",
        "workspace_id": "ws",
        "task_id": "t1",
        "goal": "Do X",
        "goal_one_line": "Do X compactly",
        "status": "in_progress",
        "next_action": "Step 1",
        "updated_at": "ts",
    }
    defaults.update(kwargs)
    conn.execute(
        """INSERT INTO tasks
           (id, workspace_id, task_id, goal, goal_one_line, status, next_action, updated_at)
           VALUES (:id, :workspace_id, :task_id, :goal, :goal_one_line,
                   :status, :next_action, :updated_at)""",
        defaults,
    )
    conn.commit()


def _seed_skill(conn: sqlite3.Connection, **kwargs) -> None:
    defaults = {
        "id": "skill_x",
        "workspace_id": "ws",
        "name": "deploy",
        "subtype": "playbook",
        "summary": "Deploy procedure",
        "when_to_use_short": "When shipping",
        "body_md": "Step 1...\nStep 2...",
        "body_token_count": 5,
        "active": 1,
        "created_at": "ts",
        "updated_at": "ts",
    }
    defaults.update(kwargs)
    conn.execute(
        """INSERT INTO skills
           (id, workspace_id, name, subtype, summary, when_to_use_short,
            body_md, body_token_count, active, created_at, updated_at)
           VALUES (:id, :workspace_id, :name, :subtype, :summary,
                   :when_to_use_short, :body_md, :body_token_count, :active,
                   :created_at, :updated_at)""",
        defaults,
    )
    conn.commit()


def test_approx_tokens_basic() -> None:
    assert approx_tokens("") == 0
    assert approx_tokens("hello world") == 2
    assert approx_tokens("  spaced  out  text  ") == 3


def test_fit_to_budget_truncates() -> None:
    lines = ["a b c", "d e f", "g h i j"]  # 3 + 3 + 4 = 10 tokens
    fit = fit_to_budget(lines, budget=6)
    assert fit == ["a b c", "d e f"]


def test_fit_to_budget_no_overflow() -> None:
    lines = ["one", "two"]
    fit = fit_to_budget(lines, budget=100)
    assert fit == lines


def test_compose_brief_empty_workspace(conn: sqlite3.Connection) -> None:
    brief = compose_brief(conn, workspace_id="ws-empty")
    assert brief.token_count > 0  # at least identity header + counts line
    assert brief.token_count <= DEFAULT_TOKEN_BUDGET
    assert any(s.name == "identity" for s in brief.sections)
    assert "ws-empty" in brief.body_md


def test_compose_brief_with_pinned_behaviors(conn: sqlite3.Connection) -> None:
    _seed_behavior(
        conn,
        id="beh_1",
        name="rule-1",
        rule_one_line="Never push without approval",
        pinned=1,
    )
    _seed_behavior(
        conn,
        id="beh_2",
        name="rule-2",
        rule_one_line="Always read before edit",
        pinned=1,
    )
    _seed_behavior(
        conn,
        id="beh_3",
        name="rule-3",
        rule_one_line="This is unpinned",
        pinned=0,
    )
    brief = compose_brief(conn, workspace_id="ws")
    assert "Never push without approval" in brief.body_md
    assert "Always read before edit" in brief.body_md
    assert "This is unpinned" not in brief.body_md  # unpinned excluded


def test_compose_brief_with_decisions(conn: sqlite3.Connection) -> None:
    _seed_decision(
        conn,
        id="dec_1",
        title="Migrate to v3",
        gist="SQL stays source of truth; compact projections default",
    )
    brief = compose_brief(conn, workspace_id="ws")
    assert "compact projections default" in brief.body_md
    assert "dec_1" in brief.body_md


def test_compose_brief_pinned_decision_marked(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id="dec_pin", title="Pinned", pinned=1, gist="P")
    _seed_decision(conn, id="dec_norm", title="Normal", pinned=0, gist="N")
    brief = compose_brief(conn, workspace_id="ws")
    assert "(pinned)" in brief.body_md


def test_compose_brief_with_task(conn: sqlite3.Connection) -> None:
    _seed_task(
        conn,
        id="task_1",
        task_id="t1",
        goal_one_line="Finish v3 Phase 1",
        status="in_progress",
        next_action="Write writer module",
    )
    brief = compose_brief(conn, workspace_id="ws")
    assert "Finish v3 Phase 1" in brief.body_md
    assert "Write writer module" in brief.body_md
    assert "in_progress" in brief.body_md


def test_compose_brief_respects_budget(conn: sqlite3.Connection) -> None:
    # Seed many pinned behaviors with long lines to force budget pressure.
    for i in range(20):
        _seed_behavior(
            conn,
            id=f"beh_{i}",
            name=f"long-rule-{i}",
            rule_one_line="word " * 30,  # ~30 tokens each
            pinned=1,
        )
    brief = compose_brief(conn, workspace_id="ws", max_tokens=200)
    assert brief.token_count <= 200, f"brief overshot budget: {brief.token_count}"


def test_compose_brief_section_order(conn: sqlite3.Connection) -> None:
    brief = compose_brief(conn, workspace_id="ws")
    names = [s.name for s in brief.sections]
    # identity must come first; behaviors before decisions; state before code_hubs.
    assert names[0] == "identity"
    if "behaviors" in names and "decisions" in names:
        assert names.index("behaviors") < names.index("decisions")
    if "state" in names and "code_hubs" in names:
        assert names.index("state") < names.index("code_hubs")


def test_compose_brief_no_active_tasks_marker(conn: sqlite3.Connection) -> None:
    brief = compose_brief(conn, workspace_id="ws")
    assert "no active tasks" in brief.body_md


def test_fetch_skill_body_returns_body_md(conn: sqlite3.Connection) -> None:
    _seed_skill(
        conn,
        id="skill_x",
        body_md="# Deploy steps\n1. Push\n2. Verify",
    )
    out = fetch_skill_body(conn, workspace_id="ws", skill_id="skill_x")
    assert out is not None
    assert "Deploy steps" in out["body_md"]


def test_fetch_skill_body_bumps_usage_count(conn: sqlite3.Connection) -> None:
    _seed_skill(conn, id="skill_x", body_md="...")
    fetch_skill_body(conn, workspace_id="ws", skill_id="skill_x")
    fetch_skill_body(conn, workspace_id="ws", skill_id="skill_x")
    fetch_skill_body(conn, workspace_id="ws", skill_id="skill_x")
    count = conn.execute("SELECT usage_count FROM skills WHERE id = ?", ("skill_x",)).fetchone()[0]
    assert count == 3


def test_fetch_skill_body_unknown_returns_none(conn: sqlite3.Connection) -> None:
    assert fetch_skill_body(conn, workspace_id="ws", skill_id="missing") is None


# ============================================================
# Identity discipline reminder
# ============================================================


def _seed_code_digest(
    conn: sqlite3.Connection, *, file_path: str, workspace_id: str = "ws"
) -> None:
    import time  # noqa: PLC0415

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        """INSERT INTO code_digests (id, workspace_id, file_path, file_sha1,
                                     language, chunk_count, symbol_count,
                                     inbound_edge_count, outbound_edge_count,
                                     purpose_short, top_symbols_json,
                                     last_indexed_at, updated_at)
           VALUES (?, ?, ?, ?, 'python', 1, 1, 0, 0, 'p', '[]', ?, ?)""",
        (f"digest_{file_path}", workspace_id, file_path, "h" * 40, ts, ts),
    )
    conn.commit()


def test_brief_identity_includes_discipline_reminder_when_digests_exist(
    conn: sqlite3.Connection,
) -> None:
    """At least one code_digest row → identity carries the impact_check rule."""
    _seed_code_digest(conn, file_path="src/x.py")
    brief = compose_brief(conn, workspace_id="ws")
    identity = next(s for s in brief.sections if s.name == "identity")
    body = "\n".join(identity.lines)
    assert "DISCIPLINE" in body
    assert "memory_v3_impact_check" in body
    assert "before Read/Edit/Grep" in body


def test_brief_identity_skips_discipline_line_when_no_digests(
    conn: sqlite3.Connection,
) -> None:
    """Empty code-graph → no discipline line (would be noise)."""
    brief = compose_brief(conn, workspace_id="ws")
    identity = next(s for s in brief.sections if s.name == "identity")
    body = "\n".join(identity.lines)
    assert "DISCIPLINE" not in body
    assert "memory_v3_impact_check" not in body


# ============================================================
# Brief cache
# ============================================================


def test_compose_brief_caches_repeat_call(conn: sqlite3.Connection) -> None:
    """Second call with same (workspace, max_tokens, fingerprint) hits cache."""
    from agent_memory_lite.v3.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    _seed_decision(conn, id="dec_a", workspace_id="ws", title="A")
    first = compose_brief(conn, workspace_id="ws")
    second = compose_brief(conn, workspace_id="ws")
    assert first.cache_hit is False
    assert second.cache_hit is True
    # Body identical between cache miss + hit.
    assert second.body_md == first.body_md


def test_compose_brief_cache_invalidates_on_write(conn: sqlite3.Connection) -> None:
    """Writing a new decision flips the workspace fingerprint → cache miss."""
    from agent_memory_lite.v3.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    _seed_decision(conn, id="dec_a", workspace_id="ws", title="A")
    first = compose_brief(conn, workspace_id="ws")
    _seed_decision(
        conn,
        id="dec_b",
        workspace_id="ws",
        title="B",
        updated_at="2026-05-17T00:01:00Z",
    )
    second = compose_brief(conn, workspace_id="ws")
    assert first.cache_hit is False
    assert second.cache_hit is False  # fingerprint flipped


def test_compose_brief_cache_isolates_workspaces(conn: sqlite3.Connection) -> None:
    """Different workspace_ids do not share cache entries."""
    from agent_memory_lite.v3.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    _seed_decision(conn, id="dec_a", workspace_id="ws_a", title="A")
    _seed_decision(conn, id="dec_b", workspace_id="ws_b", title="B")
    a1 = compose_brief(conn, workspace_id="ws_a")
    b1 = compose_brief(conn, workspace_id="ws_b")
    a2 = compose_brief(conn, workspace_id="ws_a")
    assert a1.cache_hit is False
    assert b1.cache_hit is False  # different workspace, separate entry
    assert a2.cache_hit is True


def test_compose_brief_cache_respects_max_tokens(conn: sqlite3.Connection) -> None:
    """Different max_tokens budgets do not share cache entries."""
    from agent_memory_lite.v3.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    _seed_decision(conn, id="dec_a", workspace_id="ws", title="A")
    big = compose_brief(conn, workspace_id="ws", max_tokens=500)
    small = compose_brief(conn, workspace_id="ws", max_tokens=200)
    assert big.cache_hit is False
    assert small.cache_hit is False


def test_compose_brief_cache_bounded(conn: sqlite3.Connection) -> None:
    """Cache evicts oldest entry when size exceeds _BRIEF_CACHE_MAX."""
    from agent_memory_lite.v3.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    # Force a small cache to keep the test fast.
    original_max = brief_mod._BRIEF_CACHE_MAX
    try:
        brief_mod._BRIEF_CACHE_MAX = 3
        for ws_idx in range(5):
            _seed_decision(
                conn, id=f"dec_{ws_idx}", workspace_id=f"ws_{ws_idx}", title=f"T{ws_idx}"
            )
            compose_brief(conn, workspace_id=f"ws_{ws_idx}")
        assert len(brief_mod._BRIEF_CACHE) <= 3
    finally:
        brief_mod._BRIEF_CACHE_MAX = original_max


# ============================================================
# Fingerprint regression — Cartesian product bug (2026-05-18)
# ============================================================
#
# An earlier revision used five chained LEFT JOINs on a single
# placeholder row, which made the fingerprint a Cartesian product
# over decisions x behaviors x tasks x code_digests x episodes. On
# real workspaces (copyBot: 248 x 3 x 1 x 1355 x 974 ~ 982 million
# synthetic rows) compose_brief took ~2 minutes. The v3 brief hook
# timed out before ever rendering, so copyBot's agent never saw the
# v3 pinned rules.
#
# These tests pin two invariants:
#   * fingerprint is row-count proportional, not product-of-counts
#   * the fingerprint flips when any of the watched tables mutates


def test_fingerprint_row_count_is_linear_not_cartesian(
    conn: sqlite3.Connection,
) -> None:
    """A workspace with 30 rows across watched tables must NOT cause a
    blow-up. Cartesian product would synthesise 30^5 ~ 24M rows for
    one MAX; UNION ALL stays at ~30.
    """
    import time  # noqa: PLC0415

    from agent_memory_lite.v3.cognition.brief import (  # noqa: PLC0415
        _workspace_fingerprint,
    )

    # Seed 10 decisions + 10 behaviors + 10 episodes (skip tasks /
    # code_digests so the cross-product would be zero anyway under
    # the old code — we only need to prove the surviving 3 tables
    # are not multiplied with each other).
    for i in range(10):
        _seed_decision(conn, id=f"dec_{i}", title=f"T{i}")
        _seed_behavior(conn, id=f"beh_{i}", name=f"rule-{i}")
        conn.execute(
            "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"ep_{i}", "ws", "manual", f"text {i}", f"2026-05-18T{i:02d}:00:00Z"),
        )
    conn.commit()

    t0 = time.perf_counter()
    fp = _workspace_fingerprint(conn, "ws")
    elapsed_ms = 1000 * (time.perf_counter() - t0)

    # The fingerprint is deterministic SHA1; we just assert it's
    # non-empty and the call returned promptly. The 200ms ceiling is
    # generous -- the fix lands at < 5 ms; the old Cartesian path
    # would not finish even for 10x10x10 = 1000 synthetic rows.
    assert fp
    assert len(fp) == 12
    assert elapsed_ms < 200, f"fingerprint too slow: {elapsed_ms:.0f}ms"


def test_fingerprint_flips_on_mutation(conn: sqlite3.Connection) -> None:
    """Any write to decisions/behaviors/tasks/code_digests/episodes
    must flip the fingerprint. The cache is keyed on this value, so
    a stale brief cannot survive a write.

    All inserts use ISO-8601 timestamps so the MAX() over UNION ALL
    walks them in real time order — the test fixture's default
    placeholder ``'ts'`` is lexicographically *larger* than any
    ISO string and would mask later mutations under MAX.
    """
    from agent_memory_lite.v3.cognition.brief import (  # noqa: PLC0415
        _workspace_fingerprint,
    )

    baseline = _workspace_fingerprint(conn, "ws")

    _seed_decision(
        conn,
        id="dec_new",
        title="new",
        created_at="2026-05-18T10:00:00Z",
        updated_at="2026-05-18T10:00:00Z",
    )
    after_decision = _workspace_fingerprint(conn, "ws")
    assert after_decision != baseline

    _seed_behavior(
        conn,
        id="beh_new",
        name="new-rule",
        created_at="2026-05-18T11:00:00Z",
        updated_at="2026-05-18T11:00:00Z",
    )
    after_behavior = _workspace_fingerprint(conn, "ws")
    assert after_behavior != after_decision

    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("ep_new", "ws", "manual", "text", "2026-05-18T15:00:00Z"),
    )
    conn.commit()
    after_episode = _workspace_fingerprint(conn, "ws")
    assert after_episode != after_behavior


def test_fingerprint_isolated_per_workspace(conn: sqlite3.Connection) -> None:
    """A write into ws_b must not flip ws_a's fingerprint."""
    from agent_memory_lite.v3.cognition.brief import (  # noqa: PLC0415
        _workspace_fingerprint,
    )

    fp_a_before = _workspace_fingerprint(conn, "ws_a")
    _seed_decision(conn, id="dec_b", workspace_id="ws_b", title="other-ws")
    fp_a_after = _workspace_fingerprint(conn, "ws_a")
    assert fp_a_after == fp_a_before
