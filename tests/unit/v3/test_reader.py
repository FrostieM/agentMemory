"""Unit tests for v3 reader API (memory_view / memory_get / memory_search)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.v3.storage.reader import (
    SearchHit,
    count_kind,
    get_object,
    list_kind,
    search,
    search_kind,
)

SCHEMA_V3_PATH = Path(__file__).resolve().parents[3] / "migrations" / "v3" / "0001_init.sql"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_V3_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _insert_decision(
    conn: sqlite3.Connection,
    *,
    id_: str,
    title: str,
    gist: str | None = None,
    status: str = "active",
    pinned: int = 0,
    workspace: str = "ws-test",
) -> None:
    conn.execute(
        """INSERT INTO decisions (id, workspace_id, title, decision_text, gist, status,
           valid_from, pinned, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, '2026-05-17T00:00:00Z', ?, ?, ?)""",
        (id_, workspace, title, "body", gist, status, pinned, "ts", "ts"),
    )
    conn.commit()


def _insert_behavior(
    conn: sqlite3.Connection,
    *,
    id_: str,
    name: str,
    rule: str = "rule body",
    pinned: int = 0,
    workspace: str = "ws-test",
) -> None:
    conn.execute(
        """INSERT INTO behaviors (id, workspace_id, name, kind, rule, rule_one_line,
           pinned, active, created_at, updated_at)
           VALUES (?, ?, ?, 'operating_rule', ?, ?, ?, 1, 'ts', 'ts')""",
        (id_, workspace, name, rule, rule[:80], pinned),
    )
    conn.commit()


def test_list_kind_empty_returns_empty(conn: sqlite3.Connection) -> None:
    assert list_kind(conn, workspace_id="ws-test", kind="decision") == []


def test_list_kind_returns_compact_projections(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_1", title="A", gist="Gist A")
    _insert_decision(conn, id_="dec_2", title="B", gist="Gist B")
    rows = list_kind(conn, workspace_id="ws-test", kind="decision")
    assert len(rows) == 2
    ids = {r["id"] for r in rows}
    assert ids == {"dec_1", "dec_2"}
    for row in rows:
        assert "decision_text" not in row  # full body NOT in projection


def test_list_kind_pinned_only_filter(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_p", title="Pinned", pinned=1)
    _insert_decision(conn, id_="dec_u", title="Unpinned", pinned=0)
    pinned = list_kind(conn, workspace_id="ws-test", kind="decision", pinned_only=True)
    assert len(pinned) == 1
    assert pinned[0]["id"] == "dec_p"


def test_list_kind_status_filter(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_a", title="A", status="active")
    _insert_decision(conn, id_="dec_s", title="S", status="superseded")
    rows = list_kind(conn, workspace_id="ws-test", kind="decision", status="active")
    assert len(rows) == 1
    assert rows[0]["id"] == "dec_a"


def test_list_kind_unknown_kind_returns_empty(conn: sqlite3.Connection) -> None:
    assert list_kind(conn, workspace_id="ws-test", kind="nonexistent") == []


def test_get_object_returns_compact_by_default(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_1", title="A", gist="Gist A")
    out = get_object(conn, workspace_id="ws-test", kind="decision", object_id="dec_1")
    assert out is not None
    assert out["id"] == "dec_1"
    assert "decision_text" not in out


def test_get_object_fields_opt_in_to_full_content(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_1", title="A", gist="Gist A")
    out = get_object(
        conn,
        workspace_id="ws-test",
        kind="decision",
        object_id="dec_1",
        fields=["decision_text", "rationale"],
    )
    assert out is not None
    assert "decision_text" in out
    assert out["decision_text"] == "body"


def test_get_object_unknown_id_returns_none(conn: sqlite3.Connection) -> None:
    assert get_object(conn, workspace_id="ws-test", kind="decision", object_id="missing") is None


def test_search_kind_matches_title(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_1", title="Adopt PreToolUse hook")
    _insert_decision(conn, id_="dec_2", title="Other unrelated thing")
    hits = search_kind(conn, workspace_id="ws-test", kind="decision", query="PreToolUse")
    assert len(hits) == 1
    assert hits[0].kind == "decision"
    assert hits[0].projection["id"] == "dec_1"


def test_search_kind_case_insensitive(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_1", title="UPPERCASE TITLE")
    hits = search_kind(conn, workspace_id="ws-test", kind="decision", query="uppercase")
    assert len(hits) == 1


def test_search_kind_no_match(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_1", title="A")
    hits = search_kind(conn, workspace_id="ws-test", kind="decision", query="xyz")
    assert hits == []


def test_search_multi_kind(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_1", title="Kelly sizing decision")
    _insert_behavior(conn, id_="beh_1", name="kelly-only", rule="Use Kelly only")
    hits = search(conn, workspace_id="ws-test", query="kelly", limit=10)
    kinds = {h.kind for h in hits}
    assert "decision" in kinds
    assert "behavior" in kinds


def test_search_empty_query_returns_empty(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_1", title="A")
    assert search(conn, workspace_id="ws-test", query="  ") == []


def test_search_respects_kinds_filter(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_1", title="Kelly thing")
    _insert_behavior(conn, id_="beh_1", name="kelly-rule")
    hits = search(conn, workspace_id="ws-test", query="kelly", kinds=["behavior"], limit=10)
    kinds = {h.kind for h in hits}
    assert kinds == {"behavior"}


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_a", title="Workspace A", workspace="ws-a")
    _insert_decision(conn, id_="dec_b", title="Workspace B", workspace="ws-b")
    rows_a = list_kind(conn, workspace_id="ws-a", kind="decision")
    rows_b = list_kind(conn, workspace_id="ws-b", kind="decision")
    assert {r["id"] for r in rows_a} == {"dec_a"}
    assert {r["id"] for r in rows_b} == {"dec_b"}


def test_count_kind(conn: sqlite3.Connection) -> None:
    _insert_decision(conn, id_="dec_1", title="A", pinned=1)
    _insert_decision(conn, id_="dec_2", title="B", pinned=0)
    assert count_kind(conn, workspace_id="ws-test", kind="decision") == 2
    assert count_kind(conn, workspace_id="ws-test", kind="decision", pinned_only=True) == 1


def test_count_kind_unknown_returns_zero(conn: sqlite3.Connection) -> None:
    assert count_kind(conn, workspace_id="ws-test", kind="unknown") == 0


def test_search_hit_carries_score_and_projection() -> None:
    hit = SearchHit(kind="decision", projection={"id": "x", "gist": "y"}, score=0.7)
    assert hit.score == 0.7
    assert hit.projection["id"] == "x"
