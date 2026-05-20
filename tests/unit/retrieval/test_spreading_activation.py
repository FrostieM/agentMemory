"""Phase 2: spreading_activation tests.

Covers (a) seeds with no edges return empty, (b) 1-hop direct neighbour
selection, (c) decay halves contribution per hop, (d) inhibitory cutoff
respected, (e) seeds themselves never appear in output.

v3.1 Vector 4 follow-up: causal_links traversal — when a caller opts in
via ``causal_relations``, the spreader also walks ``causal_links``, so
V4's ``semantically_similar_to`` rows drive recall through-spread
instead of just annotating the surfaced rows.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.repositories.soft_edges_repo import upsert_soft_edge
from agent_memory_lite.retrieval.spreading_activation import (
    _DEFAULT_CAUSAL_RELATIONS,
    spread,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
HEBBIAN_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0003_hebbian.sql"
CAUSAL_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0008_causal_links.sql"
)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(HEBBIAN_PATH.read_text(encoding="utf-8"))
    c.executescript(CAUSAL_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _causal(
    conn: sqlite3.Connection,
    *,
    src_kind: str,
    src_id: str,
    dst_kind: str,
    dst_id: str,
    relation: str = "semantically_similar_to",
    weight: float = 1.0,
) -> None:
    """Helper: insert one causal_link row directly."""
    conn.execute(
        """INSERT INTO causal_links (id, workspace_id, src_kind, src_id,
                                     dst_kind, dst_id, relation, weight,
                                     created_at)
           VALUES (?, 'ws', ?, ?, ?, ?, ?, ?, ?)""",
        (
            new_id(IdKind.AUDIT),
            src_kind,
            src_id,
            dst_kind,
            dst_id,
            relation,
            weight,
            iso_now(),
        ),
    )
    conn.commit()


def _edge(conn: sqlite3.Connection, a: str, b: str, weight: float = 1.0) -> None:
    """Helper: upsert one co_retrieved edge with arbitrary weight."""
    upsert_soft_edge(
        conn,
        workspace_id="ws",
        src=a,
        dst=b,
        kind="co_retrieved",
        weight_increment=weight,
    )


# ============================================================
# trivial cases
# ============================================================


def test_no_seeds_returns_empty(conn: sqlite3.Connection) -> None:
    assert spread(conn, workspace_id="ws", seeds=[]) == []


def test_zero_hops_returns_empty(conn: sqlite3.Connection) -> None:
    _edge(conn, "decision:a", "decision:b")
    out = spread(conn, workspace_id="ws", seeds=[("decision", "a", 1.0)], max_hops=0)
    assert out == []


def test_seed_with_no_edges_returns_empty(conn: sqlite3.Connection) -> None:
    out = spread(conn, workspace_id="ws", seeds=[("decision", "lonely", 1.0)])
    assert out == []


def test_seed_never_appears_in_output(conn: sqlite3.Connection) -> None:
    _edge(conn, "decision:a", "decision:b")
    out = spread(conn, workspace_id="ws", seeds=[("decision", "a", 1.0)])
    qualified = {f"{n.kind}:{n.object_id}" for n in out}
    assert "decision:a" not in qualified


# ============================================================
# 1-hop direct neighbour selection
# ============================================================


def test_one_hop_finds_direct_neighbour(conn: sqlite3.Connection) -> None:
    _edge(conn, "decision:a", "theory:t1")
    out = spread(
        conn,
        workspace_id="ws",
        seeds=[("decision", "a", 1.0)],
        max_hops=1,
    )
    assert len(out) == 1
    assert out[0].kind == "theory"
    assert out[0].object_id == "t1"
    assert out[0].hops == 1


def test_undirected_traversal(conn: sqlite3.Connection) -> None:
    """Edge inserted as a→b is reachable from either endpoint."""
    _edge(conn, "decision:a", "theory:t1")
    # Spread from t1 should reach a even though edge was a→t1.
    out = spread(conn, workspace_id="ws", seeds=[("theory", "t1", 1.0)])
    qualified = {f"{n.kind}:{n.object_id}" for n in out}
    assert "decision:a" in qualified


# ============================================================
# decay + multi-hop
# ============================================================


def test_two_hop_activation_lower_than_one_hop(conn: sqlite3.Connection) -> None:
    _edge(conn, "decision:a", "decision:b", weight=1.0)
    _edge(conn, "decision:b", "decision:c", weight=1.0)
    out = spread(
        conn,
        workspace_id="ws",
        seeds=[("decision", "a", 1.0)],
        max_hops=2,
        min_activation=0.0,
    )
    by_id = {f"{n.kind}:{n.object_id}": n.activation for n in out}
    assert "decision:b" in by_id
    assert "decision:c" in by_id
    # decay = 0.5 → c (2 hops) gets less than b (1 hop).
    assert by_id["decision:b"] > by_id["decision:c"]


def test_min_activation_prunes_deep_low_signal(conn: sqlite3.Connection) -> None:
    """At weight 1.0 + decay 0.5: 1-hop=0.5, 2-hop=0.125, 3-hop=0.0156.
    With min_activation=0.1, 3-hop must be cut."""
    _edge(conn, "decision:a", "decision:b", weight=1.0)
    _edge(conn, "decision:b", "decision:c", weight=1.0)
    _edge(conn, "decision:c", "decision:d", weight=1.0)
    out = spread(
        conn,
        workspace_id="ws",
        seeds=[("decision", "a", 1.0)],
        max_hops=3,
        min_activation=0.1,
    )
    qualified = {f"{n.kind}:{n.object_id}" for n in out}
    # b makes it; c at 0.125 makes it; d at 0.0156 does not.
    assert "decision:b" in qualified
    assert "decision:d" not in qualified


def test_inhibitory_cutoff_drops_weak_edges(conn: sqlite3.Connection) -> None:
    """Edge with weight < min_edge_weight is never traversed."""
    _edge(conn, "decision:a", "decision:weak", weight=0.05)
    _edge(conn, "decision:a", "decision:strong", weight=1.0)
    out = spread(
        conn,
        workspace_id="ws",
        seeds=[("decision", "a", 1.0)],
        min_edge_weight=0.1,
    )
    qualified = {f"{n.kind}:{n.object_id}" for n in out}
    assert "decision:strong" in qualified
    assert "decision:weak" not in qualified


# ============================================================
# bounded output
# ============================================================


def test_max_nodes_caps_output(conn: sqlite3.Connection) -> None:
    for i in range(20):
        _edge(conn, "decision:a", f"decision:n{i}", weight=1.0)
    out = spread(conn, workspace_id="ws", seeds=[("decision", "a", 1.0)], max_nodes=5)
    assert len(out) <= 5


def test_result_sorted_by_activation_desc(conn: sqlite3.Connection) -> None:
    _edge(conn, "decision:a", "decision:weak", weight=0.2)
    _edge(conn, "decision:a", "decision:strong", weight=1.5)
    _edge(conn, "decision:a", "decision:medium", weight=0.6)
    out = spread(conn, workspace_id="ws", seeds=[("decision", "a", 1.0)])
    activations = [n.activation for n in out]
    assert activations == sorted(activations, reverse=True)


# ============================================================
# causal_links traversal (v3.1 Vector 4 follow-up)
# ============================================================


def test_causal_link_not_traversed_by_default(conn: sqlite3.Connection) -> None:
    """Without ``causal_relations``, only soft_edges drive spread.
    This keeps the brief's ``_build_associates`` path unchanged."""
    _causal(
        conn,
        src_kind="decision",
        src_id="a",
        dst_kind="decision",
        dst_id="similar",
        relation="semantically_similar_to",
        weight=1.0,
    )
    out = spread(conn, workspace_id="ws", seeds=[("decision", "a", 1.0)])
    qualified = {f"{n.kind}:{n.object_id}" for n in out}
    assert "decision:similar" not in qualified


def test_causal_link_drives_spread_when_opted_in(conn: sqlite3.Connection) -> None:
    """V4's ``semantically_similar_to`` row reaches a neighbour even
    when no soft_edges row exists — this is the Vector 4 path
    recall.py opts into."""
    _causal(
        conn,
        src_kind="decision",
        src_id="a",
        dst_kind="decision",
        dst_id="similar",
        relation="semantically_similar_to",
        weight=1.0,
    )
    out = spread(
        conn,
        workspace_id="ws",
        seeds=[("decision", "a", 1.0)],
        causal_relations=_DEFAULT_CAUSAL_RELATIONS,
    )
    qualified = {f"{n.kind}:{n.object_id}" for n in out}
    assert "decision:similar" in qualified


def test_causal_traversal_is_bidirectional(conn: sqlite3.Connection) -> None:
    """``semantically_similar_to`` is stored as ``earlier -> later`` but
    seeding the later node still reaches the earlier one."""
    _causal(
        conn,
        src_kind="decision",
        src_id="earlier",
        dst_kind="decision",
        dst_id="later",
        relation="semantically_similar_to",
    )
    out = spread(
        conn,
        workspace_id="ws",
        seeds=[("decision", "later", 1.0)],
        causal_relations=_DEFAULT_CAUSAL_RELATIONS,
    )
    qualified = {f"{n.kind}:{n.object_id}" for n in out}
    assert "decision:earlier" in qualified


def test_causal_relation_filter_respected(conn: sqlite3.Connection) -> None:
    """A relation not in the caller's allowlist is invisible to spread.
    Lets callers narrow to e.g. only V4 semantic links if they want."""
    _causal(
        conn,
        src_kind="decision",
        src_id="a",
        dst_kind="decision",
        dst_id="invalidated_one",
        relation="invalidated_by",
    )
    out = spread(
        conn,
        workspace_id="ws",
        seeds=[("decision", "a", 1.0)],
        causal_relations=("semantically_similar_to",),
    )
    qualified = {f"{n.kind}:{n.object_id}" for n in out}
    assert "decision:invalidated_one" not in qualified


def test_causal_inhibitory_cutoff_respected(conn: sqlite3.Connection) -> None:
    """A causal_link below ``min_edge_weight`` is not traversed."""
    _causal(
        conn,
        src_kind="decision",
        src_id="a",
        dst_kind="decision",
        dst_id="weak_causal",
        relation="semantically_similar_to",
        weight=0.05,
    )
    out = spread(
        conn,
        workspace_id="ws",
        seeds=[("decision", "a", 1.0)],
        causal_relations=_DEFAULT_CAUSAL_RELATIONS,
        min_edge_weight=0.1,
    )
    qualified = {f"{n.kind}:{n.object_id}" for n in out}
    assert "decision:weak_causal" not in qualified


def test_soft_and_causal_combine(conn: sqlite3.Connection) -> None:
    """Soft edge AND causal link from the same seed both surface their
    respective targets in one pass."""
    _edge(conn, "decision:a", "decision:soft_hit", weight=1.0)
    _causal(
        conn,
        src_kind="decision",
        src_id="a",
        dst_kind="decision",
        dst_id="causal_hit",
        relation="semantically_similar_to",
    )
    out = spread(
        conn,
        workspace_id="ws",
        seeds=[("decision", "a", 1.0)],
        causal_relations=_DEFAULT_CAUSAL_RELATIONS,
    )
    qualified = {f"{n.kind}:{n.object_id}" for n in out}
    assert "decision:soft_hit" in qualified
    assert "decision:causal_hit" in qualified


def test_failure_soft_when_causal_table_missing() -> None:
    """Pre-migration DB has soft_edges but no causal_links — caller
    opting in must NOT raise; soft-edge spreading still works."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(HEBBIAN_PATH.read_text(encoding="utf-8"))
    # Deliberately skip CAUSAL_PATH.
    try:
        upsert_soft_edge(
            c,
            workspace_id="ws",
            src="decision:a",
            dst="decision:b",
            kind="co_retrieved",
            weight_increment=1.0,
        )
        out = spread(
            c,
            workspace_id="ws",
            seeds=[("decision", "a", 1.0)],
            causal_relations=_DEFAULT_CAUSAL_RELATIONS,
        )
        qualified = {f"{n.kind}:{n.object_id}" for n in out}
        assert "decision:b" in qualified
    finally:
        c.close()
