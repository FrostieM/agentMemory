"""Round-2 audit #5: recall batch helpers eliminate N+1 SQL fan-out.

Pre-fix ``recall()`` looped over N activation nodes and called
``get_object`` + ``list_outgoing`` once per node = 1 + 2N round-trips.
Post-fix the loop groups nodes by kind and uses two new batch helpers
(``get_objects_batch`` + ``list_outgoing_batch``) that collapse the
fan-out to 2 queries per distinct kind.

This test exercises the helpers directly with a count-and-trace probe.
The helpers are simple enough to verify in isolation: any regression
that re-introduces per-id queries would fail the assert here. The
``recall()``-level wiring is covered by ``test_recall.py`` end-to-end.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.retrieval.causal_extractor import list_outgoing_batch
from agent_memory_lite.storage.reader import get_objects_batch
from agent_memory_lite.utils.time import iso_now

SCHEMA = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
OUTCOME = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
CAUSAL = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0008_causal_links.sql"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA.read_text(encoding="utf-8"))
    c.executescript(OUTCOME.read_text(encoding="utf-8"))
    c.executescript(CAUSAL.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_decisions(conn: sqlite3.Connection, count: int) -> list[str]:
    """Seed ``count`` minimal decisions in workspace 'ws'."""
    ids: list[str] = []
    now = iso_now()
    for i in range(count):
        dec_id = f"dec_{i:03d}"
        ids.append(dec_id)
        conn.execute(
            """INSERT INTO decisions
               (id, workspace_id, title, decision_text, gist, status, valid_from,
                created_at, updated_at, outcome_score, pinned)
               VALUES (?, 'ws', ?, ?, ?, 'active', ?, ?, ?, 0.5, 0)""",
            (dec_id, f"title {i}", f"body {i}", f"gist {i}", now, now, now),
        )
    conn.commit()
    return ids


def _seed_causal_links(conn: sqlite3.Connection, src_ids: list[str]) -> None:
    """Give each src_id one outgoing causal_link so the batch helper
    actually has rows to return."""
    now = iso_now()
    for i, src in enumerate(src_ids):
        conn.execute(
            """INSERT INTO causal_links
               (id, workspace_id, src_kind, src_id, dst_kind, dst_id, relation,
                weight, evidence_episode_id, created_at)
               VALUES (?, 'ws', 'decision', ?, 'decision', ?, 'derived_from',
                       0.5, NULL, ?)""",
            (f"cl_{i:03d}", src, src_ids[(i + 1) % len(src_ids)], now),
        )
    conn.commit()


def test_get_objects_batch_uses_one_query_for_many_ids(
    conn: sqlite3.Connection,
) -> None:
    """20 decisions → batch helper should issue exactly ONE SQL query.
    Pre-fix the recall loop made 20 separate get_object SELECTs here."""
    ids = _seed_decisions(conn, 20)

    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        projections = get_objects_batch(conn, workspace_id="ws", kind="decision", object_ids=ids)
    finally:
        conn.set_trace_callback(None)

    assert len(projections) == 20, "must surface all 20 rows"
    # The batch helper issues one SELECT. Allow up to 2 to cover any
    # implicit BEGIN/COMMIT, but never per-id (which would be 20+).
    assert len(statements) <= 2, (
        f"get_objects_batch ran {len(statements)} statements for 20 ids "
        f"(should be 1 — N+1 regression)"
    )


def test_list_outgoing_batch_uses_one_query_for_many_src_ids(
    conn: sqlite3.Connection,
) -> None:
    """15 source ids → batch helper should issue ONE SQL query for the
    causal-links lookup. Pre-fix the recall loop ran one per source."""
    ids = _seed_decisions(conn, 15)
    _seed_causal_links(conn, ids)

    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        links = list_outgoing_batch(conn, workspace_id="ws", src_kind="decision", src_ids=ids)
    finally:
        conn.set_trace_callback(None)

    # Every source must appear in the result dict (even if no rows).
    assert set(links.keys()) == set(ids)
    # Total link rows summed across sources equals what we seeded.
    total = sum(len(rows) for rows in links.values())
    assert total == len(ids)
    assert len(statements) <= 2, (
        f"list_outgoing_batch ran {len(statements)} statements for 15 src_ids"
    )


def test_get_objects_batch_empty_inputs_short_circuit(
    conn: sqlite3.Connection,
) -> None:
    """No ids → no SQL. Cheap defensive guard for the recall caller
    that filters out empty kind groups before batching."""
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        result = get_objects_batch(conn, workspace_id="ws", kind="decision", object_ids=[])
    finally:
        conn.set_trace_callback(None)
    assert result == {}
    assert statements == []


def test_list_outgoing_batch_handles_missing_table(
    conn: sqlite3.Connection,
) -> None:
    """Pre-migration DB without causal_links table → empty dict, no
    crash. Failure-soft mirrors ``list_outgoing``."""
    conn.execute("DROP TABLE causal_links")
    conn.commit()
    result = list_outgoing_batch(conn, workspace_id="ws", src_kind="decision", src_ids=["a", "b"])
    assert result == {}


def test_get_objects_batch_deduplicates_ids(conn: sqlite3.Connection) -> None:
    """Duplicate input ids must collapse into a single SQL parameter
    set so we don't waste bind slots on a noisy caller."""
    _seed_decisions(conn, 3)
    result = get_objects_batch(
        conn,
        workspace_id="ws",
        kind="decision",
        object_ids=["dec_000", "dec_000", "dec_001", "dec_001", "dec_002"],
    )
    assert set(result.keys()) == {"dec_000", "dec_001", "dec_002"}
