"""Unit tests for v3 reader API (memory_view / memory_get / memory_search)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.storage.reader import (
    _MAX_SEARCH_TOKENS,
    SearchHit,
    _search_tokens,
    count_kind,
    get_object,
    list_kind,
    search,
    search_kind,
)


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


def test_list_kind_unknown_kind_raises(conn: sqlite3.Connection) -> None:
    """Round-2 audit: an unknown kind used to return [] — indistinguishable
    from a genuine empty result. A plural typo (kind='decisions') then
    silently 'found nothing'. list_kind / get_object now raise ValueError
    so the typo / enum-drift is loud."""
    with pytest.raises(ValueError, match="unknown kind 'nonexistent'"):
        list_kind(conn, workspace_id="ws-test", kind="nonexistent")


def test_get_object_unknown_kind_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="unknown kind 'decisions'"):
        # plural typo — the exact enum-drift shape the guard targets
        get_object(conn, workspace_id="ws-test", kind="decisions", object_id="x")


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


def test_search_kind_multi_word_non_contiguous(conn: sqlite3.Connection) -> None:
    """A multi-word query matches when the row carries the tokens — even
    out of order. The old whole-query LIKE only matched a verbatim
    substring of the entire query, so this returned nothing."""
    _insert_decision(conn, id_="dec_1", title="Plan storage redesign for steps")
    hits = search_kind(conn, workspace_id="ws-test", kind="decision", query="storage redesign plan")
    assert len(hits) == 1
    assert hits[0].projection["id"] == "dec_1"


def test_search_kind_ranks_by_token_coverage(conn: sqlite3.Connection) -> None:
    """A row matching more distinct query tokens ranks above one matching fewer."""
    _insert_decision(conn, id_="dec_all", title="alpha beta gamma delta")
    _insert_decision(conn, id_="dec_some", title="alpha beta only")
    hits = search_kind(
        conn, workspace_id="ws-test", kind="decision", query="alpha beta gamma delta"
    )
    assert [h.projection["id"] for h in hits] == ["dec_all", "dec_some"]
    assert hits[0].score > hits[1].score


def test_search_kind_only_short_tokens_returns_empty(conn: sqlite3.Connection) -> None:
    """A query of only sub-2-char tokens has nothing to match on."""
    _insert_decision(conn, id_="dec_1", title="A B C")
    assert search_kind(conn, workspace_id="ws-test", kind="decision", query="a b c") == []


def test_search_tokens_dedup_trim_minlen() -> None:
    """_search_tokens lower-cases, trims punctuation, dedups, drops <2-char."""
    assert _search_tokens("Plan, plan STORAGE x") == ["plan", "storage"]
    assert _search_tokens("ui db") == ["ui", "db"]
    assert _search_tokens("   ") == []


def test_search_multi_word_finds_decision(conn: sqlite3.Connection) -> None:
    """End-to-end through search(): a multi-word query reaches a decision —
    the regression that silently defeated discipline rule #2."""
    _insert_decision(conn, id_="dec_1", title="Replace flat plan with first-class steps")
    hits = search(conn, workspace_id="ws-test", query="first-class plan steps", limit=10)
    assert any(h.projection["id"] == "dec_1" for h in hits)


def test_search_kind_best_match_survives_large_candidate_set(conn: sqlite3.Connection) -> None:
    """The genuine top match ranks #1 even when many rows share a common
    token — the in-SQL match-count ORDER BY, not an arbitrary window."""
    for i in range(60):
        _insert_decision(conn, id_=f"dec_c{i}", title="common topic")
    _insert_decision(conn, id_="dec_best", title="common rare topic")
    hits = search_kind(conn, workspace_id="ws-test", kind="decision", query="common rare", limit=5)
    assert hits[0].projection["id"] == "dec_best"
    assert hits[0].score > hits[1].score


def test_search_kind_underscore_is_literal(conn: sqlite3.Connection) -> None:
    """An underscore in a token matches a literal underscore, not LIKE's
    any-char wildcard (escaped LIKE)."""
    _insert_decision(conn, id_="dec_lit", title="the plan_steps table")
    _insert_decision(conn, id_="dec_wild", title="the planXsteps table")
    hits = search_kind(conn, workspace_id="ws-test", kind="decision", query="plan_steps")
    assert [h.projection["id"] for h in hits] == ["dec_lit"]


def test_search_kind_score_stays_below_chunk_band(conn: sqlite3.Connection) -> None:
    """search_kind scores cap at 0.5 — below the 0.8 chunk-FTS band — so
    this interim path never outranks a real BM25 chunk hit."""
    _insert_decision(conn, id_="dec_1", title="alpha beta gamma")
    hits = search_kind(conn, workspace_id="ws-test", kind="decision", query="alpha beta gamma")
    assert hits[0].score == 0.5  # full token coverage -> exactly the cap


def test_search_tokens_caps_token_count() -> None:
    """A pathological many-word query is capped at _MAX_SEARCH_TOKENS."""
    many = " ".join(f"tok{i}" for i in range(200))
    assert len(_search_tokens(many)) == _MAX_SEARCH_TOKENS


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


def test_search_unknown_kind_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="unknown kinds"):
        search(conn, workspace_id="ws-test", query="kelly", kinds=["decisions"], limit=10)


def test_archived_decision_hidden_from_list_and_search_but_gettable(
    conn: sqlite3.Connection,
) -> None:
    _insert_decision(conn, id_="dec_archived", title="Archive needle")
    conn.execute(
        "UPDATE decisions SET status = 'archived' WHERE id = ?",
        ("dec_archived",),
    )
    conn.commit()

    assert list_kind(conn, workspace_id="ws-test", kind="decision") == []
    assert search(conn, workspace_id="ws-test", query="archive needle", limit=10) == []

    out = get_object(
        conn,
        workspace_id="ws-test",
        kind="decision",
        object_id="dec_archived",
        fields=["status"],
    )
    assert out is not None
    assert out["status"] == "archived"


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
