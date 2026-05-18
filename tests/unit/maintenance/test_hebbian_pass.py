"""Phase 2: hebbian_pass distillation tests.

Covers (a) the math (1/(rank_a*rank_b) weight contributions, ratchet),
(b) the HeLa-Mem outcome gate, (c) the order-stable src<dst pairing,
(d) idempotency on empty staging.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.maintenance.hebbian_pass import (
    distill_workspace,
    qualified,
)
from agent_memory_lite.retrieval.coactivation_log import log_coactivation
from agent_memory_lite.storage.reader import SearchHit
from agent_memory_lite.utils.time import iso_now

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
OUTCOME_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
)
HEBBIAN_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0003_hebbian.sql"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(OUTCOME_PATH.read_text(encoding="utf-8"))
    c.executescript(HEBBIAN_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _hit(kind: str, item_id: str) -> SearchHit:
    return SearchHit(kind=kind, projection={"id": item_id, "kind": kind}, score=0.5)


def _seed_decision(conn: sqlite3.Connection, *, id_: str, outcome: float = 0.5) -> None:
    """Seed a positive-outcome decision so the HeLa-Mem gate doesn't block."""
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, 'ws', 't', 'b', 'g', 'active', ?, ?, ?, ?, 0)""",
        (id_, iso_now(), iso_now(), iso_now(), outcome),
    )
    conn.commit()


# ============================================================
# qualified-name helper
# ============================================================


def test_qualified_format() -> None:
    assert qualified("decision", "dec_a") == "decision:dec_a"
    assert qualified("theory", "th_1") == "theory:th_1"


# ============================================================
# math: pair-weight accumulation
# ============================================================


def test_pair_gets_weight_1_when_both_rank_1_in_two_queries(conn: sqlite3.Connection) -> None:
    """Top-1 + top-2 fires 1/(1*2)=0.5 per query → 1.0 across two."""
    _seed_decision(conn, id_="dec_a", outcome=0.5)
    _seed_decision(conn, id_="dec_b", outcome=0.5)
    hits = [_hit("decision", "dec_a"), _hit("decision", "dec_b")]
    log_coactivation(conn, workspace_id="ws", query="topic x", hits=hits)
    log_coactivation(conn, workspace_id="ws", query="topic y", hits=hits)
    upserted, gated = distill_workspace(conn, workspace_id="ws")
    assert upserted == 2  # one pair x two query groups
    assert gated == 0
    row = conn.execute("SELECT weight FROM soft_edges WHERE edge_kind = 'co_retrieved'").fetchone()
    assert row is not None
    assert row["weight"] == pytest.approx(1.0, abs=1e-6)


def test_pair_weight_lower_for_deeper_ranks(conn: sqlite3.Connection) -> None:
    """rank=1+2 → 0.5, rank=2+3 → 0.166 — deeper pairs strengthen less."""
    _seed_decision(conn, id_="dec_top1", outcome=0.5)
    _seed_decision(conn, id_="dec_top2", outcome=0.5)
    _seed_decision(conn, id_="dec_top3", outcome=0.5)
    hits = [
        _hit("decision", "dec_top1"),
        _hit("decision", "dec_top2"),
        _hit("decision", "dec_top3"),
    ]
    log_coactivation(conn, workspace_id="ws", query="q", hits=hits)
    distill_workspace(conn, workspace_id="ws")
    rows = {
        (r["src_qualified_name"], r["dst_qualified_name"]): r["weight"]
        for r in conn.execute(
            "SELECT src_qualified_name, dst_qualified_name, weight FROM soft_edges"
        )
    }
    # Pair top1+top2 = 1/(1*2) = 0.5; top1+top3 = 1/(1*3) = 0.333; top2+top3 = 1/(2*3) = 0.166.
    weights = sorted(rows.values(), reverse=True)
    assert weights[0] == pytest.approx(0.5, abs=1e-3)
    assert weights[1] == pytest.approx(0.333, abs=1e-3)
    assert weights[2] == pytest.approx(0.166, abs=1e-3)


# ============================================================
# HeLa-Mem outcome gate
# ============================================================


def test_gate_blocks_pair_with_both_outcomes_zero(conn: sqlite3.Connection) -> None:
    """Two outcome_score=0.0 items must NOT be linked."""
    _seed_decision(conn, id_="dec_x", outcome=0.0)
    _seed_decision(conn, id_="dec_y", outcome=0.0)
    log_coactivation(
        conn,
        workspace_id="ws",
        query="q",
        hits=[_hit("decision", "dec_x"), _hit("decision", "dec_y")],
    )
    upserted, gated = distill_workspace(conn, workspace_id="ws", outcome_gate=True)
    assert upserted == 0
    assert gated == 1


def test_gate_allows_pair_with_one_positive(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_pos", outcome=0.5)
    _seed_decision(conn, id_="dec_neutral", outcome=0.0)
    log_coactivation(
        conn,
        workspace_id="ws",
        query="q",
        hits=[_hit("decision", "dec_pos"), _hit("decision", "dec_neutral")],
    )
    upserted, gated = distill_workspace(conn, workspace_id="ws", outcome_gate=True)
    assert upserted == 1
    assert gated == 0


def test_gate_off_allows_everything(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_x", outcome=0.0)
    _seed_decision(conn, id_="dec_y", outcome=0.0)
    log_coactivation(
        conn,
        workspace_id="ws",
        query="q",
        hits=[_hit("decision", "dec_x"), _hit("decision", "dec_y")],
    )
    upserted, gated = distill_workspace(conn, workspace_id="ws", outcome_gate=False)
    assert upserted == 1
    assert gated == 0


# ============================================================
# order stability + idempotency
# ============================================================


def test_pair_storage_is_order_stable(conn: sqlite3.Connection) -> None:
    """Whichever order hits arrive, the resulting edge has the same (src, dst)."""
    _seed_decision(conn, id_="dec_a", outcome=0.5)
    _seed_decision(conn, id_="dec_b", outcome=0.5)
    log_coactivation(
        conn,
        workspace_id="ws",
        query="q1",
        hits=[_hit("decision", "dec_a"), _hit("decision", "dec_b")],
    )
    log_coactivation(
        conn,
        workspace_id="ws",
        query="q2",
        hits=[_hit("decision", "dec_b"), _hit("decision", "dec_a")],
    )
    distill_workspace(conn, workspace_id="ws")
    rows = conn.execute(
        "SELECT src_qualified_name, dst_qualified_name, weight FROM soft_edges"
    ).fetchall()
    # Exactly one edge regardless of input order; src lex < dst.
    assert len(rows) == 1
    assert rows[0]["src_qualified_name"] == "decision:dec_a"
    assert rows[0]["dst_qualified_name"] == "decision:dec_b"


def test_second_pass_on_drained_log_writes_nothing(conn: sqlite3.Connection) -> None:
    """The pass distills but does NOT delete the log -- second run computes
    the same edges (idempotent ratchet up)."""
    _seed_decision(conn, id_="dec_a", outcome=0.5)
    _seed_decision(conn, id_="dec_b", outcome=0.5)
    log_coactivation(
        conn,
        workspace_id="ws",
        query="q",
        hits=[_hit("decision", "dec_a"), _hit("decision", "dec_b")],
    )
    first_up, _ = distill_workspace(conn, workspace_id="ws")
    second_up, _ = distill_workspace(conn, workspace_id="ws")
    # Ratchet: weight increases on the same edge.
    assert first_up == 1
    assert second_up == 1
    row = conn.execute("SELECT weight, observation_count FROM soft_edges").fetchone()
    assert row["weight"] == pytest.approx(1.0, abs=1e-3)
    assert row["observation_count"] == 2


def test_distill_empty_workspace_returns_zero(conn: sqlite3.Connection) -> None:
    assert distill_workspace(conn, workspace_id="ws") == (0, 0)
