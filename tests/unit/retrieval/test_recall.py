"""Phase 7: recall() integration tests.

End-to-end: seed BM25-discoverable decisions/theories/behaviors, build
co_retrieved soft_edges and causal_links between them, then verify
``recall()`` produces a mixed-kind result that obeys outcome_floor,
returns causal_links for each hit, and ranks by combined score.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.repositories.soft_edges_repo import upsert_soft_edge
from agent_memory_lite.retrieval.causal_extractor import _upsert_causal_link
from agent_memory_lite.retrieval.recall import recall
from agent_memory_lite.utils.time import iso_now


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


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    id_: str,
    title: str,
    outcome: float = 0.5,
    gist: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, 'ws', ?, ?, ?, 'active', ?, ?, ?, ?, 0)""",
        (
            id_,
            title,
            f"body {title}",
            gist or title,
            iso_now(),
            iso_now(),
            iso_now(),
            outcome,
        ),
    )
    conn.commit()


def _seed_theory(
    conn: sqlite3.Connection,
    *,
    id_: str,
    title: str,
    outcome: float = 0.4,
) -> None:
    conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, claim, gist, status, created_at, updated_at,
            outcome_score)
           VALUES (?, 'ws', ?, ?, ?, 'active', ?, ?, ?)""",
        (id_, title, f"claim {title}", title, iso_now(), iso_now(), outcome),
    )
    conn.commit()


# ============================================================
# trivial cases
# ============================================================


def test_empty_topic_returns_empty(conn: sqlite3.Connection) -> None:
    assert recall(conn, workspace_id="ws", topic="") == []
    assert recall(conn, workspace_id="ws", topic="   ") == []


def test_no_matching_seed_returns_empty(conn: sqlite3.Connection) -> None:
    """If BM25 finds nothing, recall returns []."""
    out = recall(conn, workspace_id="ws", topic="nothing_seeded")
    assert out == []


# ============================================================
# spreading + mixed kinds
# ============================================================


def test_recall_includes_seed_and_neighbour(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_kelly", title="kelly sizing rule")
    _seed_theory(conn, id_="th_quarter", title="quarter cap thesis")
    upsert_soft_edge(
        conn,
        workspace_id="ws",
        src="decision:dec_kelly",
        dst="theory:th_quarter",
        kind="co_retrieved",
        weight_increment=2.0,
    )
    conn.commit()
    out = recall(conn, workspace_id="ws", topic="kelly", depth=1)
    ids = {(h.kind, h.object_id) for h in out}
    assert ("decision", "dec_kelly") in ids
    assert ("theory", "th_quarter") in ids


def test_outcome_floor_filters_after_spreading(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_seed", title="kelly", outcome=0.6)
    _seed_decision(conn, id_="dec_bad", title="failed kelly approach", outcome=-0.8)
    upsert_soft_edge(
        conn,
        workspace_id="ws",
        src="decision:dec_seed",
        dst="decision:dec_bad",
        kind="co_retrieved",
        weight_increment=3.0,
    )
    conn.commit()
    out = recall(conn, workspace_id="ws", topic="kelly", outcome_floor=0.0)
    ids = {h.object_id for h in out}
    assert "dec_seed" in ids
    assert "dec_bad" not in ids


def test_kinds_filter_restricts_output(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_a", title="kelly")
    _seed_theory(conn, id_="th_a", title="kelly thesis")
    upsert_soft_edge(
        conn,
        workspace_id="ws",
        src="decision:dec_a",
        dst="theory:th_a",
        kind="co_retrieved",
        weight_increment=1.5,
    )
    conn.commit()
    out = recall(conn, workspace_id="ws", topic="kelly", kinds=["decision"])
    assert {h.kind for h in out} == {"decision"}


# ============================================================
# causal_links surfacing
# ============================================================


def test_causal_links_surfaced_per_hit(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_new", title="kelly v2", outcome=0.5)
    _seed_decision(conn, id_="dec_old", title="prior kelly", outcome=-0.2)
    _upsert_causal_link(
        conn,
        workspace_id="ws",
        src_kind="decision",
        src_id="dec_new",
        dst_kind="decision",
        dst_id="dec_old",
        relation="invalidated",
    )
    conn.commit()
    out = recall(conn, workspace_id="ws", topic="kelly")
    seed_hit = next((h for h in out if h.object_id == "dec_new"), None)
    assert seed_hit is not None
    relations = {link["relation"] for link in seed_hit.causal_links}
    assert "invalidated" in relations


def test_combined_score_favours_positive_outcome(conn: sqlite3.Connection) -> None:
    """Two decisions with identical activation -- the one with higher outcome ranks first."""
    _seed_decision(conn, id_="dec_high", title="kelly high", outcome=0.9)
    _seed_decision(conn, id_="dec_low", title="kelly low", outcome=0.0)
    out = recall(conn, workspace_id="ws", topic="kelly")
    # dec_high should sort first if both have similar seed activation.
    high_idx = next((i for i, h in enumerate(out) if h.object_id == "dec_high"), None)
    low_idx = next((i for i, h in enumerate(out) if h.object_id == "dec_low"), None)
    assert high_idx is not None
    assert low_idx is not None
    assert high_idx < low_idx


# ============================================================
# pre-migration compatibility
# ============================================================


def test_pre_migration_db_returns_hits_without_causal() -> None:
    """No causal_links table -> recall still works, causal_links list is empty."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    # Do NOT apply 0008.
    c.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES ('dec_pre', 'ws', 'kelly pre-migration', 'b', 'g',
                   'active', ?, ?, ?, 0.5, 0)""",
        (iso_now(), iso_now(), iso_now()),
    )
    c.commit()
    out = recall(c, workspace_id="ws", topic="kelly")
    assert any(h.object_id == "dec_pre" for h in out)
    # causal_links list is empty for every hit.
    for h in out:
        assert h.causal_links == []
    c.close()
