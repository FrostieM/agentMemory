"""prune_soft_edges removes single-observation, stale CODE edges only.

The memory/Hebbian edges (co_retrieved / co_mentioned) that feed memory_recall +
the brief's associates must survive even at observation_count=1, and reinforced
(>= 2) or recent code edges must survive too. The prune is bounded and a bad
timestamp is a safe no-op.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.maintenance.soft_edge_prune import prune_soft_edges

NOW = "2026-06-22T00:00:00+00:00"
OLD = "2026-04-01T00:00:00+00:00"  # > 30 days before NOW
RECENT = "2026-06-20T00:00:00+00:00"  # < 30 days before NOW


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    c = open_connection(":memory:")  # type: ignore[arg-type]
    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _seed(
    conn: sqlite3.Connection,
    *,
    id_: str,
    kind: str,
    oc: int = 1,
    last_seen: str = OLD,
    src: str = "mod.a",
    dst: str = "mod.b",
) -> None:
    conn.execute(
        """INSERT INTO soft_edges
           (id, workspace_id, src_qualified_name, dst_qualified_name,
            edge_kind, weight, observation_count, last_seen_at, created_at, metadata_json)
           VALUES (?, 'ws', ?, ?, ?, 0.5, ?, ?, ?, '{}')""",
        (id_, src, dst, kind, oc, last_seen, last_seen),
    )
    conn.commit()


def _ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT id FROM soft_edges")}


def _prune(conn: sqlite3.Connection, *, max_oc: int = 1, min_age: int = 30, cap: int = 5000):
    return prune_soft_edges(
        conn,
        workspace_id="ws",
        max_observation_count=max_oc,
        min_age_days=min_age,
        cap=cap,
        now_iso=NOW,
    )


def test_prunes_stale_single_observation_code_edge(conn: sqlite3.Connection) -> None:
    _seed(conn, id_="e1", kind="co_changed", oc=1, last_seen=OLD)
    _seed(conn, id_="e2", kind="similar_signature", oc=1, last_seen=OLD)
    result = _prune(conn)
    assert result.pruned == 2
    assert _ids(conn) == set()


def test_keeps_memory_edges_even_single_observation(conn: sqlite3.Connection) -> None:
    """The recall/brief associative graph must never be pruned here."""
    _seed(conn, id_="m1", kind="co_retrieved", src="decision:a", dst="decision:b")
    _seed(conn, id_="m2", kind="co_mentioned", src="decision:a", dst="decision:c")
    result = _prune(conn)
    assert result.pruned == 0
    assert _ids(conn) == {"m1", "m2"}


def test_keeps_reinforced_code_edge(conn: sqlite3.Connection) -> None:
    _seed(conn, id_="e2", kind="similar_signature", oc=2, last_seen=OLD)
    result = _prune(conn)
    assert result.pruned == 0
    assert "e2" in _ids(conn)


def test_keeps_recent_code_edge(conn: sqlite3.Connection) -> None:
    _seed(conn, id_="e3", kind="co_changed", oc=1, last_seen=RECENT)
    result = _prune(conn)
    assert result.pruned == 0
    assert "e3" in _ids(conn)


def test_bounded_by_cap(conn: sqlite3.Connection) -> None:
    for i in range(10):
        _seed(conn, id_=f"c{i}", kind="co_changed", src=f"s{i}", dst=f"d{i}")
    result = _prune(conn, cap=3)
    assert result.pruned == 3
    assert len(_ids(conn)) == 7


def test_bad_timestamp_is_noop(conn: sqlite3.Connection) -> None:
    _seed(conn, id_="e4", kind="co_changed", oc=1, last_seen=OLD)
    result = prune_soft_edges(
        conn,
        workspace_id="ws",
        max_observation_count=1,
        min_age_days=30,
        cap=5000,
        now_iso="not-a-date",
    )
    assert result.pruned == 0
    assert "e4" in _ids(conn)
