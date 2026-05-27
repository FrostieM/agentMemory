"""Phase 2 wiring test: reader.search() logs coactivations as a side-effect.

End-to-end check that storage.reader.search inserts rows into
retrieval_coactivation when hebbian_enabled is on, and that the
opt-out (log_coactivations=False) suppresses the side-effect for
internal callers (brief, recall handler).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.storage.reader import search
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


def _seed_decision(conn: sqlite3.Connection, *, id_: str, title: str) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, pinned)
           VALUES (?, 'ws', ?, 'body', 'g', 'active', ?, ?, ?, 0)""",
        (id_, title, iso_now(), iso_now(), iso_now()),
    )
    conn.commit()


def _count_coact(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM retrieval_coactivation").fetchone()[0])


def test_search_logs_coactivations_when_enabled(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_1", title="kelly sizing experiment")
    _seed_decision(conn, id_="dec_2", title="kelly portfolio rule")
    hits = search(conn, workspace_id="ws", query="kelly", kinds=["decision"], limit=10)
    assert len(hits) >= 2
    assert _count_coact(conn) >= 2


def test_search_opt_out_skips_logging(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_1", title="kelly sizing")
    search(
        conn,
        workspace_id="ws",
        query="kelly",
        kinds=["decision"],
        log_coactivations=False,
    )
    assert _count_coact(conn) == 0


def test_search_empty_query_does_not_log(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_1", title="kelly")
    search(conn, workspace_id="ws", query="", kinds=["decision"])
    assert _count_coact(conn) == 0


def test_search_no_hits_does_not_log(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_1", title="kelly")
    search(conn, workspace_id="ws", query="nonexistent_token_xyz", kinds=["decision"])
    assert _count_coact(conn) == 0
