"""Unit tests for scripts/seed_v3_discipline.py.

Covers:

* All 3 rules inserted on fresh DB
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
from scripts import seed_v3_discipline as seed

SCHEMA_V3_PATH = Path(__file__).resolve().parents[3] / "migrations" / "v3" / "0001_init.sql"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "v3.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_V3_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return path


# ============================================================
# seed_discipline
# ============================================================


def test_seed_inserts_three_rules_on_fresh_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        results = seed.seed_discipline(conn, workspace_id="ws")
    finally:
        conn.close()
    assert len(results) == 3
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
    assert len(rows) == 3
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
    assert len(payload) == 3
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
