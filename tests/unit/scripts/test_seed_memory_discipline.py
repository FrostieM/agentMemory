"""Unit tests for scripts/seed_memory_discipline.py.

Covers:

* All 4 rules inserted on fresh DB
* Re-running is idempotent: every rule reports 'skipped' on second pass
* Each inserted rule is pinned=1 + active=1
* applies_to_json round-trips through JSON
* end-to-end main() with --json output
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from scripts import seed_memory_discipline as seed


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "canonical.db"
    conn = sqlite3.connect(path)
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(conn)
    conn.commit()
    conn.close()
    return path


# ============================================================
# seed_discipline
# ============================================================


def test_seed_inserts_four_rules_on_fresh_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        results = seed.seed_discipline(conn, workspace_id="ws")
    finally:
        conn.close()
    assert len(results) == 4
    statuses = [r.status for r in results]
    assert all(s == "inserted" for s in statuses)


def test_seed_is_idempotent_on_second_pass(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        seed.seed_discipline(conn, workspace_id="ws")
        results = seed.seed_discipline(conn, workspace_id="ws")
    finally:
        conn.close()
    statuses = [r.status for r in results]
    assert all(s == "skipped" for s in statuses)


def test_seeded_rules_are_pinned_and_active(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        seed.seed_discipline(conn, workspace_id="ws")
        rows = conn.execute(
            "SELECT name, pinned, active FROM behaviors WHERE workspace_id = ?",
            ("ws",),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 4
    for _, pinned, active in rows:
        assert pinned == 1
        assert active == 1


def test_seeded_rules_have_graph_first_rule(db_path: Path) -> None:
    """The cornerstone rule must mention impact_check + Read/Edit/Grep."""
    conn = sqlite3.connect(db_path)
    try:
        seed.seed_discipline(conn, workspace_id="ws")
        row = conn.execute(
            "SELECT rule, rule_one_line FROM behaviors "
            "WHERE workspace_id = ? AND name = 'graph-tools-first'",
            ("ws",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    rule, one_line = row
    assert "memory_impact_check" in rule
    assert "Read/Edit/Grep" in rule
    assert "impact_check" in one_line


def test_seeded_rules_have_plan_rule(db_path: Path) -> None:
    """The plan-use rule must name plan_steps + the active-step convention."""
    conn = sqlite3.connect(db_path)
    try:
        seed.seed_discipline(conn, workspace_id="ws")
        row = conn.execute(
            "SELECT rule, rule_one_line FROM behaviors "
            "WHERE workspace_id = ? AND name = 'maintain-plan-steps'",
            ("ws",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    rule, one_line = row
    assert "plan_steps" in rule
    assert "active" in rule
    assert "plan_steps" in one_line


class _CapturingLog:
    """Records ``.warning(event, **kw)`` calls; no-ops every other logger method."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def warning(self, event: str, **_kw: object) -> None:
        self.events.append(event)

    def __getattr__(self, _name: str) -> object:
        return lambda *_a, **_k: None


def test_seed_rule_observes_failed_fts_sync(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Certification finding: _insert_rule was the only Batch B sync_durable_fts call
    site ignoring the bool. On a forced sync rollback (returns False), the seed must
    surface a warning instead of reporting a clean 'inserted' silently."""
    import agent_memory_lite.fts.durable_fts as dfts  # noqa: PLC0415

    monkeypatch.setattr(dfts, "sync_durable_fts", lambda *_a, **_k: False)
    cap = _CapturingLog()
    monkeypatch.setattr(seed, "_log", cap)
    conn = sqlite3.connect(db_path)
    try:
        results = seed.seed_discipline(conn, workspace_id="ws")
    finally:
        conn.close()
    assert all(r.status == "inserted" for r in results)  # rows still committed
    assert "durable_fts_sync_skipped_on_seed_rule" in cap.events  # but the gap is loud


def test_seeded_discipline_rules_are_fts_searchable(db_path: Path) -> None:
    """M1 (write-atomicity batch): seed_discipline raw-INSERTs pinned discipline
    behaviors (bypassing the self-syncing business writer + write_canonical), so it
    owns the durable_fts sync. These rules are the highest-trust habits a freshly-
    registered agent relies on; without the sync they're silently invisible to
    memory_search. The cornerstone 'graph-tools-first' rule must be findable."""
    from agent_memory_lite.storage.reader import search_kind_fts  # noqa: PLC0415

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        results = seed.seed_discipline(conn, workspace_id="ws")
        graph_rule_id = next(r.id for r in results if r.name == "graph-tools-first")
        hits = search_kind_fts(
            conn, workspace_id="ws", kind="behavior", query="impact_check Read Edit Grep", limit=10
        )
    finally:
        conn.close()
    assert graph_rule_id in [h.projection["id"] for h in hits]


def test_seeded_applies_to_json_round_trips(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        seed.seed_discipline(conn, workspace_id="ws")
        rows = conn.execute(
            "SELECT name, applies_to_json FROM behaviors WHERE workspace_id = ?",
            ("ws",),
        ).fetchall()
    finally:
        conn.close()
    for name, applies_json in rows:
        parsed = json.loads(applies_json)
        assert isinstance(parsed, list)
        assert len(parsed) >= 1, f"{name}: applies_to should be non-empty"


def test_seed_workspace_isolation(db_path: Path) -> None:
    """Seeding ws_a must not leak into ws_b."""
    conn = sqlite3.connect(db_path)
    try:
        seed.seed_discipline(conn, workspace_id="ws_a")
        b_results = seed.seed_discipline(conn, workspace_id="ws_b")
    finally:
        conn.close()
    # ws_b is fresh — every rule should insert.
    assert all(r.status == "inserted" for r in b_results)


# ============================================================
# main()
# ============================================================


def test_main_json_output(db_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = seed.main(["--workspace", "ws", "--db-path", str(db_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 4
    assert all(item["status"] == "inserted" for item in payload)


def test_main_human_output(db_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = seed.main(["--workspace", "ws", "--db-path", str(db_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "graph-tools-first" in out
    assert "inserted" in out


def test_main_missing_db_returns_two(tmp_path: Path) -> None:
    rc = seed.main(["--workspace", "ws", "--db-path", str(tmp_path / "nope.db"), "--json"])
    assert rc == 2
