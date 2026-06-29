"""Batch C: consolidated cross-workspace read-leak regression suite.

One SQLite DB can host more than one ``workspace_id`` namespace (a hub DB, or a
co-resident anchor). A read for workspace A must NEVER surface workspace B's rows
across any read path. These tests pin the workspace scoping that the read-isolation
work relies on -- a regression here is a silent cross-workspace leak (the read
analogue of the 2026-05-21 pollution incident), not just a flaky test.

Scope here is the SQL/FTS read paths (durable_fts, chunks_fts) + the
maintenance_events degradation surface. The connection/workspace mismatch guard
(ensure_workspace_readable_db / the MCP _read_guard) is exercised in
tests/unit/api/test_workspace_routing.py and tests/unit/mcp/test_memory_handlers.py.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.api.routes.memory_status import build_environment
from agent_memory_lite.config.settings import get_settings
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.fts.chunks_fts import insert_chunk_fts
from agent_memory_lite.ingestion.canonical_writer import write_canonical
from agent_memory_lite.ingestion.maintenance_writer import write_maintenance_event
from agent_memory_lite.models.maintenance import MaintenanceEventIn, MaintenanceSeverity
from agent_memory_lite.repositories.maintenance_queries import (
    count_open_maintenance_events,
    list_open_maintenance_events,
)
from agent_memory_lite.storage.reader import search_chunks_fts, search_kind_fts


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def test_durable_fts_search_is_workspace_isolated(conn: sqlite3.Connection) -> None:
    """The SAME token under two workspaces: each durable-FTS search sees only its own
    row (reader.search_kind_fts filters f.workspace_id). A leak would surface both."""
    a = write_canonical(
        conn,
        workspace_id="ws_a",
        kind="decision",
        payload={"title": "kangaroo plan", "decision_text": "kangaroo body"},
    )
    b = write_canonical(
        conn,
        workspace_id="ws_b",
        kind="decision",
        payload={"title": "kangaroo plan", "decision_text": "kangaroo body"},
    )
    assert a is not None
    assert b is not None
    hits_a = search_kind_fts(conn, workspace_id="ws_a", kind="decision", query="kangaroo", limit=10)
    assert [h.projection["id"] for h in hits_a] == [a["id"]]
    hits_b = search_kind_fts(conn, workspace_id="ws_b", kind="decision", query="kangaroo", limit=10)
    assert [h.projection["id"] for h in hits_b] == [b["id"]]


def _seed_chunk_row(conn: sqlite3.Connection, *, chunk_id: str, ws: str, text: str) -> None:
    conn.execute(
        "INSERT INTO chunks (id, workspace_id, file_id, text, embedding_id, kind, created_at) "
        "VALUES (?, ?, NULL, ?, NULL, 'code', '2026-06-29T00:00:00+00:00')",
        (chunk_id, ws, text),
    )


def test_chunks_fts_search_is_workspace_isolated(conn: sqlite3.Connection) -> None:
    """The same chunk token under two workspaces: chunk FTS search returns only the
    requesting workspace's chunk (reader.search_chunks_fts filters f.workspace_id).
    Asserted by count -- a leak would return 2 hits per workspace, not 1."""
    for ws, cid in (("ws_a", "ca"), ("ws_b", "cb")):
        _seed_chunk_row(conn, chunk_id=cid, ws=ws, text="platypus token")
        insert_chunk_fts(
            conn,
            chunk_id=cid,
            workspace_id=ws,
            path=None,
            symbols=[],
            text="platypus token",
            summary=None,
        )
    conn.commit()
    assert len(search_chunks_fts(conn, workspace_id="ws_a", query="platypus", limit=10)) == 1
    assert len(search_chunks_fts(conn, workspace_id="ws_b", query="platypus", limit=10)) == 1


def test_build_environment_distinguishes_corrupt_registry_from_absent(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Batch C observability: GET /memory/status (build_environment) must surface a
    CORRUPT registry as registry_load_error='corrupt:*', not as an empty
    registry_workspaces that looks like "no workspaces registered" -- so an operator
    sees the real root cause of mis-routing. An ABSENT registry stays a clean None."""
    corrupt = tmp_path / "workspaces.json"
    corrupt.write_text("not json", encoding="utf-8")
    env = build_environment(conn, get_settings().model_copy(update={"workspaces_file": corrupt}))
    assert env.registry_load_error == "corrupt:json"
    assert env.registry_workspaces == []

    env_absent = build_environment(
        conn, get_settings().model_copy(update={"workspaces_file": tmp_path / "absent.json"})
    )
    assert env_absent.registry_load_error is None


def test_maintenance_events_reads_are_workspace_isolated(conn: sqlite3.Connection) -> None:
    """An ERROR degradation event under ws_a must NOT surface on ws_b's reads -- the
    degradation banner (Batch A) reads these, so a leak would show ws_a's degradation
    on ws_b's status/brief. Every listing read is WHERE workspace_id = ?."""
    write_maintenance_event(
        conn,
        MaintenanceEventIn(
            workspace_id="ws_a",
            kind="durable_fts_sync_failed",
            severity=MaintenanceSeverity.ERROR,
            summary="ws_a substrate degradation",
        ),
    )
    assert count_open_maintenance_events(conn, workspace_id="ws_a") == 1
    assert count_open_maintenance_events(conn, workspace_id="ws_b") == 0
    assert list_open_maintenance_events(conn, workspace_id="ws_b") == []
    a_events = list_open_maintenance_events(conn, workspace_id="ws_a")
    assert [e.workspace_id for e in a_events] == ["ws_a"]
