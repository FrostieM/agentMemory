"""Phase 2: log_coactivation tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.retrieval.coactivation_log import (
    log_coactivation,
    prune_log,
    query_hash,
)
from agent_memory_lite.storage.reader import SearchHit
from agent_memory_lite.utils.time import iso_now

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
HEBBIAN_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0003_hebbian.sql"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(HEBBIAN_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _hit(kind: str, item_id: str, score: float = 0.5) -> SearchHit:
    return SearchHit(kind=kind, projection={"id": item_id, "kind": kind}, score=score)


# ============================================================
# query_hash — stability + normalization
# ============================================================


def test_query_hash_normalizes_whitespace_and_case() -> None:
    assert query_hash("Kelly Sizing") == query_hash("kelly sizing")
    assert query_hash("kelly  sizing\n") == query_hash("kelly sizing")


def test_query_hash_distinct_topics_differ() -> None:
    a = query_hash("kelly sizing")
    b = query_hash("calibrator volatility")
    assert a != b
    assert len(a) == 16
    assert len(b) == 16


def test_query_hash_empty_returns_empty() -> None:
    assert query_hash("") == ""
    assert query_hash("   ") == ""


# ============================================================
# log_coactivation — happy path + failure-soft behavior
# ============================================================


def test_log_coactivation_writes_one_row_per_hit(conn: sqlite3.Connection) -> None:
    hits = [_hit("decision", "dec_1"), _hit("theory", "th_1"), _hit("behavior", "beh_1")]
    n = log_coactivation(conn, workspace_id="ws", query="kelly sizing", hits=hits)
    assert n == 3
    rows = conn.execute(
        "SELECT item_kind, item_id, rank FROM retrieval_coactivation ORDER BY rank"
    ).fetchall()
    assert [r["item_kind"] for r in rows] == ["decision", "theory", "behavior"]
    assert [r["rank"] for r in rows] == [1, 2, 3]


def test_log_coactivation_same_query_groups_under_same_hash(conn: sqlite3.Connection) -> None:
    log_coactivation(conn, workspace_id="ws", query="kelly", hits=[_hit("decision", "a")])
    log_coactivation(conn, workspace_id="ws", query="Kelly\n", hits=[_hit("theory", "b")])
    hashes = {
        r["query_hash"] for r in conn.execute("SELECT query_hash FROM retrieval_coactivation")
    }
    assert len(hashes) == 1


def test_log_coactivation_empty_query_noop(conn: sqlite3.Connection) -> None:
    n = log_coactivation(conn, workspace_id="ws", query="", hits=[_hit("decision", "x")])
    assert n == 0


def test_log_coactivation_caps_at_max_hits(conn: sqlite3.Connection) -> None:
    hits = [_hit("decision", f"dec_{i}") for i in range(20)]
    n = log_coactivation(conn, workspace_id="ws", query="q", hits=hits, max_hits=5)
    assert n == 5


def test_log_coactivation_failure_soft_on_missing_table() -> None:
    """No retrieval_coactivation table → returns 0, does not raise."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Don't apply 0003_hebbian.sql. Log call must silently no-op.
    n = log_coactivation(c, workspace_id="ws", query="q", hits=[_hit("decision", "x")])
    assert n == 0
    c.close()


# ============================================================
# prune_log — retention horizon
# ============================================================


def test_prune_log_removes_rows_older_than_retention(conn: sqlite3.Connection) -> None:
    # Insert an old row (created_at = 2020) and a fresh one.
    now = iso_now()
    old = "2020-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO retrieval_coactivation "
        "(id, workspace_id, query_hash, item_kind, item_id, rank, created_at) "
        "VALUES (?, 'ws', 'h', 'decision', 'dec_old', 1, ?)",
        ("a", old),
    )
    conn.execute(
        "INSERT INTO retrieval_coactivation "
        "(id, workspace_id, query_hash, item_kind, item_id, rank, created_at) "
        "VALUES (?, 'ws', 'h', 'decision', 'dec_new', 2, ?)",
        ("b", now),
    )
    conn.commit()
    deleted = prune_log(conn, workspace_id="ws", retention_days=14, now_iso=now)
    assert deleted == 1
    remaining = conn.execute(
        "SELECT item_id FROM retrieval_coactivation ORDER BY item_id"
    ).fetchall()
    assert [r["item_id"] for r in remaining] == ["dec_new"]


def test_prune_log_zero_retention_returns_zero(conn: sqlite3.Connection) -> None:
    assert prune_log(conn, workspace_id="ws", retention_days=0) == 0
