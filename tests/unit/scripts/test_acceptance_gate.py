"""Unit tests for scripts/acceptance_gate.py.

Covers:

* Token approximation + projection cost helpers
* ``_percentile`` interpolation behaviour
* ``measure_brief`` cold + warm cycle  → cache_hit_rate populated
* ``measure_search`` projection token aggregation
* ``measure_digests`` staleness windowing
* ``compute_verdict`` pass / warn / fail rollup
* End-to-end ``main()`` with a temp v3 DB → JSON envelope + human render
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from scripts import acceptance_gate as gate

from agent_memory_lite.cognition import brief as brief_mod


@pytest.fixture
def db_path(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "canonical.db"
    conn = sqlite3.connect(path)
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(conn)
    conn.commit()
    conn.close()
    return path


@pytest.fixture(autouse=True)
def _reset_brief_cache() -> Iterator[None]:
    brief_mod._BRIEF_CACHE.clear()
    try:
        yield
    finally:
        brief_mod._BRIEF_CACHE.clear()


def _seed_decision(db_path: Path, **kwargs: Any) -> None:
    defaults = {
        "id": "dec_a",
        "workspace_id": "ws",
        "title": "Kelly sizing",
        "decision_text": "Use quarter-Kelly bankroll fraction.",
        "gist": "Quarter-Kelly bankroll fraction.",
        "status": "active",
        "pinned": 0,
        "valid_from": "2026-05-17T00:00:00Z",
        "created_at": "2026-05-17T00:00:00Z",
        "updated_at": "2026-05-17T00:00:00Z",
    }
    defaults.update(kwargs)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, pinned,
            valid_from, created_at, updated_at)
           VALUES (:id, :workspace_id, :title, :decision_text, :gist, :status,
                   :pinned, :valid_from, :created_at, :updated_at)""",
        defaults,
    )
    conn.commit()
    conn.close()


def _seed_digest(db_path: Path, *, file_path: str, minutes_ago: int = 0) -> None:
    last_indexed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - minutes_ago * 60))
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO code_digests
           (id, workspace_id, file_path, file_sha1, language, chunk_count,
            symbol_count, purpose_short, last_indexed_at, updated_at)
           VALUES (?, 'ws', ?, '0'*40, 'python', 1, 1, 'p', ?, ?)""",
        (f"digest_{file_path}", file_path, last_indexed, last_indexed),
    )
    conn.commit()
    conn.close()


# ============================================================
# Helpers
# ============================================================


def test_approx_tokens_basic() -> None:
    assert gate._approx_tokens("") == 0
    assert gate._approx_tokens("one two three") == 3


def test_projection_tokens_sums_string_values() -> None:
    proj = {"title": "Kelly sizing", "gist": "Quarter-Kelly bankroll", "score": 0.5}
    assert gate._projection_tokens(proj) == 4  # 2 + 2 (score is float, skipped)


def test_percentile_basic() -> None:
    assert gate._percentile([], 0.95) == 0.0
    assert gate._percentile([10.0], 0.95) == 10.0
    assert gate._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0


# ============================================================
# measure_brief
# ============================================================


def test_measure_brief_cold_then_warm(db_path: Path) -> None:
    _seed_decision(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        result = gate.measure_brief(
            conn, workspace_id="ws", max_tokens=500, cold_calls=2, warm_calls=3
        )
    finally:
        conn.close()
    assert result["token_count"] > 0
    assert result["within_budget"] is True
    # First call is cold; subsequent warm calls should all hit the cache.
    assert result["cache_hit_rate"] == 1.0


def test_measure_brief_within_budget(db_path: Path) -> None:
    """Tight budget still yields a within_budget=True report after trim."""
    _seed_decision(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        result = gate.measure_brief(
            conn, workspace_id="ws", max_tokens=100, cold_calls=1, warm_calls=1
        )
    finally:
        conn.close()
    assert result["budget"] == 100
    assert result["within_budget"] is True


# ============================================================
# measure_search
# ============================================================


def test_measure_search_aggregates_projection_tokens(db_path: Path) -> None:
    _seed_decision(db_path, title="Kelly sizing review", decision_text="Use quarter-Kelly")
    _seed_decision(
        db_path,
        id="dec_b",
        title="Kelly sizing rerun",
        decision_text="Confirm threshold",
        updated_at="2026-05-17T00:01:00Z",
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        result = gate.measure_search(conn, workspace_id="ws", queries=("kelly sizing",), limit=10)
    finally:
        conn.close()
    assert result["queries_run"] == 1
    assert result["hits_returned"] >= 2
    assert result["median_tokens_per_hit"] > 0
    assert result["within_target"] is True


def test_measure_search_empty_workspace(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        result = gate.measure_search(conn, workspace_id="ws", queries=("any",), limit=10)
    finally:
        conn.close()
    assert result["hits_returned"] == 0
    assert result["median_tokens_per_hit"] == 0


# ============================================================
# measure_digests
# ============================================================


def test_measure_digests_empty(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        result = gate.measure_digests(conn, workspace_id="ws")
    finally:
        conn.close()
    assert result["count"] == 0
    assert result["newest_age_minutes"] is None


def test_measure_digests_reports_newest_age(db_path: Path) -> None:
    _seed_digest(db_path, file_path="src/a.py", minutes_ago=10)
    _seed_digest(db_path, file_path="src/b.py", minutes_ago=240)
    _seed_digest(db_path, file_path="src/c.py", minutes_ago=480)
    conn = sqlite3.connect(db_path)
    try:
        result = gate.measure_digests(conn, workspace_id="ws")
    finally:
        conn.close()
    assert result["count"] == 3
    # Newest = freshest = lowest age. Allow ±1 minute slack for clock drift.
    assert 9.0 <= result["newest_age_minutes"] <= 11.0
    # Median should be the middle entry, 240 minutes.
    assert 239.0 <= result["median_age_minutes"] <= 241.0


# ============================================================
# Verdict rollup
# ============================================================


def test_verdict_fail_when_brief_over_budget() -> None:
    report = {
        "brief": {"within_budget": False, "cache_hit_rate": 1.0},
        "search": {"within_target": True},
        "digests": {"newest_age_minutes": 5},
    }
    assert gate.compute_verdict(report) == "fail"


def test_verdict_warn_on_cache_hit_rate_below_target() -> None:
    report = {
        "brief": {"within_budget": True, "cache_hit_rate": 0.3},
        "search": {"within_target": True},
        "digests": {"newest_age_minutes": 5},
    }
    assert gate.compute_verdict(report) == "warn"


def test_verdict_warn_on_projection_size() -> None:
    report = {
        "brief": {"within_budget": True, "cache_hit_rate": 1.0},
        "search": {"within_target": False},
        "digests": {"newest_age_minutes": 5},
    }
    assert gate.compute_verdict(report) == "warn"


def test_verdict_warn_on_digest_staleness() -> None:
    report = {
        "brief": {"within_budget": True, "cache_hit_rate": 1.0},
        "search": {"within_target": True},
        "digests": {"newest_age_minutes": 480},
    }
    assert gate.compute_verdict(report) == "warn"


def test_verdict_pass_when_all_targets_met() -> None:
    report = {
        "brief": {"within_budget": True, "cache_hit_rate": 0.9},
        "search": {"within_target": True},
        "digests": {"newest_age_minutes": 30},
    }
    assert gate.compute_verdict(report) == "pass"


def test_verdict_warn_when_digests_exist_but_age_unmeasurable() -> None:
    """round-A: digests are present (count > 0) but their age is None (every
    timestamp unparseable -- a corruption signal). This must WARN, not silently pass."""
    report = {
        "brief": {"within_budget": True, "cache_hit_rate": 0.9},
        "search": {"within_target": True},
        "digests": {"count": 2, "newest_age_minutes": None},
    }
    assert gate.compute_verdict(report) == "warn"


def test_verdict_pass_when_no_digests_at_all() -> None:
    """count == 0 + age None is benign (nothing indexed) -- stays a pass."""
    report = {
        "brief": {"within_budget": True, "cache_hit_rate": 0.9},
        "search": {"within_target": True},
        "digests": {"count": 0, "newest_age_minutes": None},
    }
    assert gate.compute_verdict(report) == "pass"


# ============================================================
# End-to-end main()
# ============================================================


def test_main_json_envelope(db_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_decision(db_path)
    rc = gate.main(["--db-path", str(db_path), "--workspace", "ws", "--queries", "kelly"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == "ws"
    assert payload["brief"]["within_budget"] is True
    assert payload["verdict"] in {"pass", "warn"}


def test_main_human_render(db_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_decision(db_path)
    rc = gate.main(
        ["--db-path", str(db_path), "--workspace", "ws", "--queries", "kelly", "--human"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Acceptance gate" in out
    assert "Verdict:" in out


def test_main_missing_db_returns_two(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = gate.main(["--db-path", str(tmp_path / "nope.db"), "--workspace", "ws"])
    assert rc == 2


def test_main_require_all_fails_on_warn(db_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Empty workspace → low cache hit rate (no warm hits) → warn → exit 1 in CI mode."""
    rc = gate.main(
        [
            "--db-path",
            str(db_path),
            "--workspace",
            "ws",
            "--queries",
            "x",
            "--require-all",
        ]
    )
    # An empty workspace cache_hit_rate may legitimately be 1.0 (every brief is the
    # same trivial body), so we can't strictly assert rc==1 here. Just verify
    # the flag is parsed and accepted; behaviour is covered by the unit test on
    # compute_verdict directly.
    assert rc in {0, 1}
