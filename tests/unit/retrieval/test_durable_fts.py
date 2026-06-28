"""FTS5/BM25 relevance retrieval for the durable knowledge kinds (0007).

Covers the table + backfill (migration), the create/edit sync wired into the two
write choke points (write_canonical for the business-writer kinds, edit for all),
OR + BM25 ranking, the archive filter on the FTS JOIN, the LIKE fallback, and the
rebuild helper.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.fts.durable_fts import rebuild_durable_fts
from agent_memory_lite.ingestion.canonical_writer import write_canonical
from agent_memory_lite.storage.reader import search, search_kind_fts
from agent_memory_lite.storage.writer import edit


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _decision(conn: sqlite3.Connection, title: str, text: str) -> str:
    out = write_canonical(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"title": title, "decision_text": text},
    )
    assert out is not None
    return str(out["id"])


class _FailDurableInsertConn(sqlite3.Connection):
    """A connection that fails INSERTs into ``durable_fts`` on demand.

    Lets a test drive the sync rollback path deterministically while every
    other statement (seeding, SELECT, DELETE, SAVEPOINT/RELEASE) passes
    through. Mirrors the production connection (autocommit) so ``with_tx``
    opens a real BEGIN/COMMIT around the re-index.
    """

    fail_durable_insert = False

    def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:  # type: ignore[override]
        if self.fail_durable_insert and isinstance(sql, str) and "INSERT INTO durable_fts" in sql:
            raise sqlite3.OperationalError("forced durable_fts insert failure")
        return super().execute(sql, parameters)


def _ids(hits: list) -> list[str]:
    return [h.projection["id"] for h in hits]


def test_migration_creates_durable_fts_table(conn: sqlite3.Connection) -> None:
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='durable_fts'"
        ).fetchone()
        is not None
    )


def test_create_via_business_writer_is_fts_indexed(conn: sqlite3.Connection) -> None:
    """decision routes through write_decision_canonical (bypasses storage.writer);
    write_canonical must still sync its FTS content."""
    did = _decision(conn, "Adopt quarter-Kelly sizing", "Cap position size for the bankroll")
    hits = search_kind_fts(
        conn, workspace_id="ws", kind="decision", query="kelly bankroll", limit=5
    )
    assert did in _ids(hits)


def test_create_via_storage_writer_else_branch_is_fts_indexed(conn: sqlite3.Connection) -> None:
    """concept routes through the storage_write else-branch of write_canonical."""
    out = write_canonical(
        conn,
        workspace_id="ws",
        kind="concept",
        payload={"name": "Reciprocal Rank Fusion", "definition": "a rank aggregation method"},
    )
    assert out is not None
    hits = search_kind_fts(
        conn, workspace_id="ws", kind="concept", query="reciprocal fusion", limit=5
    )
    assert out["id"] in _ids(hits)


def test_edit_resyncs_fts_content(conn: sqlite3.Connection) -> None:
    did = _decision(conn, "Widget plan", "about widgets")
    assert not search_kind_fts(conn, workspace_id="ws", kind="decision", query="sprocket", limit=5)
    edit(
        conn,
        workspace_id="ws",
        kind="decision",
        object_id=did,
        fields={"decision_text": "now about sprocket assemblies"},
    )
    hits = search_kind_fts(conn, workspace_id="ws", kind="decision", query="sprocket", limit=5)
    assert did in _ids(hits)


class _CapturingLog:
    """Records ``.warning(event, **kw)`` calls; no-ops every other logger method."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def warning(self, event: str, **_kw: object) -> None:
        self.events.append(event)

    def __getattr__(self, _name: str) -> object:
        return lambda *_a, **_k: None


def test_edit_observes_and_logs_failed_resync(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3 (audit rounds 1-2): edit() CAPTURES the sync_durable_fts bool (was
    silently discarded) and logs ``durable_fts_sync_skipped_on_edit`` when the
    re-sync rolls back. The edit's row mutation still stands."""
    did = _decision(conn, "Widget plan", "about widgets")
    cap = _CapturingLog()
    monkeypatch.setattr("agent_memory_lite.storage.writer._log", cap)
    # sync_durable_fts is a local import in edit(); patch the source attribute.
    monkeypatch.setattr("agent_memory_lite.fts.durable_fts.sync_durable_fts", lambda *a, **k: False)
    out = edit(
        conn,
        workspace_id="ws",
        kind="decision",
        object_id=did,
        fields={"decision_text": "now about sprocket assemblies"},
    )
    assert out is not None  # edit completes despite the failed re-sync
    row = conn.execute("SELECT decision_text FROM decisions WHERE id = ?", (did,)).fetchone()
    assert row["decision_text"] == "now about sprocket assemblies"  # mutation stands
    assert "durable_fts_sync_skipped_on_edit" in cap.events  # observed, not silent


def test_rollback_observes_and_logs_failed_resync(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3: rollback() mirrors edit() -- it observes the sync bool and logs
    ``durable_fts_sync_skipped_on_rollback`` on a rolled-back re-sync."""
    from agent_memory_lite.storage.writer import rollback  # noqa: PLC0415

    did = _decision(conn, "zebra title", "use the zebra approach")
    edit(
        conn,
        workspace_id="ws",
        kind="decision",
        object_id=did,
        fields={"decision_text": "now the giraffe approach"},
    )
    cap = _CapturingLog()
    monkeypatch.setattr("agent_memory_lite.storage.writer._log", cap)
    monkeypatch.setattr("agent_memory_lite.fts.durable_fts.sync_durable_fts", lambda *a, **k: False)
    rollback(conn, workspace_id="ws", kind="decision", object_id=did, to_version=1, why="test")
    assert "durable_fts_sync_skipped_on_rollback" in cap.events


def test_create_sync_failure_records_degradation_event(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch A L4: a durable_fts sync that rolls back on a CREATE records a
    DB-backed ERROR maintenance_event (durable_fts_sync_failed), so the failure
    is VISIBLE on memory_status/health -- not only in the structlog stream."""
    from agent_memory_lite.repositories.maintenance_degradation import (  # noqa: PLC0415
        open_maintenance_degradations,
    )

    # canonical_writer imports sync_durable_fts at module top (unlike edit()'s
    # local import), so patch the name bound IN canonical_writer.
    monkeypatch.setattr(
        "agent_memory_lite.ingestion.canonical_writer.sync_durable_fts", lambda *a, **k: False
    )
    _decision(conn, "kelly degradation case", "quarter kelly sizing")
    rows = open_maintenance_degradations(conn, workspace_id="ws")
    assert any(kind == "durable_fts_sync_failed" and sev == "error" for kind, sev, _ in rows)


def test_or_semantics_and_bm25_ranking(conn: sqlite3.Connection) -> None:
    """OR matches a row with ANY query token; BM25 ranks a row matching more (and
    rarer) tokens above one matching only a single shared token."""
    both = _decision(conn, "alpha beta plan", "alpha beta gamma considerations")
    one = _decision(conn, "beta only plan", "beta and some other content")
    hits = search_kind_fts(conn, workspace_id="ws", kind="decision", query="alpha beta", limit=5)
    ids = _ids(hits)
    assert both in ids
    assert one in ids  # OR: the beta-only row still surfaces
    assert ids.index(both) < ids.index(one)  # BM25: the 2-token match ranks first


def test_archived_row_excluded_from_fts_search(conn: sqlite3.Connection) -> None:
    """The FTS JOIN re-applies _append_archive_filter on the source table, so an
    archived decision is dropped even though its FTS row still exists."""
    did = _decision(conn, "secret kelly tactic", "quarter kelly only")
    edit(conn, workspace_id="ws", kind="decision", object_id=did, fields={"status": "archived"})
    hits = search_kind_fts(conn, workspace_id="ws", kind="decision", query="kelly tactic", limit=5)
    assert did not in _ids(hits)


def test_search_falls_back_to_like_when_fts_misses(conn: sqlite3.Connection) -> None:
    """A partial-token (substring) query has no whole-token FTS match, so the
    durable path returns nothing and search() falls back to the LIKE floor --
    the change is strictly additive (no query that worked before regresses)."""
    _decision(conn, "Calibration policy", "recalibrate the threshold each week")
    assert not search_kind_fts(conn, workspace_id="ws", kind="decision", query="calibrat", limit=5)
    hits = search(
        conn,
        workspace_id="ws",
        query="calibrat",
        kinds=["decision"],
        limit=5,
        log_coactivations=False,
    )
    assert hits  # LIKE substring match still finds it


def test_rebuild_repopulates_after_corruption(conn: sqlite3.Connection) -> None:
    did = _decision(conn, "kelly rebuild case", "quarter kelly sizing")
    conn.execute("DELETE FROM durable_fts")
    conn.commit()
    assert not search_kind_fts(conn, workspace_id="ws", kind="decision", query="kelly", limit=5)
    written = rebuild_durable_fts(conn, workspace_id="ws")
    conn.commit()
    assert written >= 1
    assert did in _ids(
        search_kind_fts(conn, workspace_id="ws", kind="decision", query="kelly", limit=5)
    )


def test_auto_promoted_behavior_is_fts_indexed(conn: sqlite3.Connection) -> None:
    """The consolidation auto-promote path INSERTs a behavior directly (bypassing
    write_canonical/edit); it must sync FTS or the pinned behavior is invisible to
    search whenever another FTS row matches the query."""
    from agent_memory_lite.compaction.promote_insight_to_behavior import (  # noqa: PLC0415
        _insert_behavior_from_insight,
    )
    from agent_memory_lite.utils.time import iso_now  # noqa: PLC0415

    conn.execute(
        "INSERT INTO insights (id, workspace_id, insight_type, summary, gist, status, "
        "confidence, created_at, updated_at) VALUES ('ins1', 'ws', 'lesson', "
        "'always run kangaroo checks before the sprint', 'kangaroo lesson', "
        "'candidate', 0.9, ?, ?)",
        (iso_now(), iso_now()),
    )
    row = conn.execute("SELECT * FROM insights WHERE id = 'ins1'").fetchone()
    bid = _insert_behavior_from_insight(conn, workspace_id="ws", insight_row=row)
    conn.commit()
    hits = search_kind_fts(
        conn, workspace_id="ws", kind="behavior", query="kangaroo sprint", limit=5
    )
    assert bid in _ids(hits)


def test_rollback_resyncs_fts(conn: sqlite3.Connection) -> None:
    """rollback restores the FTS-indexed text columns; the index must follow."""
    from agent_memory_lite.storage.writer import rollback  # noqa: PLC0415

    did = _decision(conn, "zebra title", "use the zebra approach")
    edit(
        conn,
        workspace_id="ws",
        kind="decision",
        object_id=did,
        fields={"title": "giraffe title", "decision_text": "use the giraffe approach"},
    )
    assert did in _ids(
        search_kind_fts(conn, workspace_id="ws", kind="decision", query="giraffe", limit=5)
    )
    rollback(conn, workspace_id="ws", kind="decision", object_id=did, to_version=1, why="test")
    assert did in _ids(
        search_kind_fts(conn, workspace_id="ws", kind="decision", query="zebra", limit=5)
    )
    assert did not in _ids(
        search_kind_fts(conn, workspace_id="ws", kind="decision", query="giraffe", limit=5)
    )


def test_sync_failure_is_atomic_and_surfaced_not_silent() -> None:
    """Reliability audit 2026-06-25: a durable_fts INSERT failure must NOT leave
    the row de-indexed, and must NOT be swallowed silently.

    Pre-fix: connections are autocommit, so sync auto-committed the DELETE then
    the INSERT *separately* -- a failed INSERT permanently de-indexed the row
    (silently unsearchable) and the caller never knew. Post-fix the DELETE +
    INSERT run inside one ``with_tx`` unit: a failed INSERT rolls the DELETE
    back (the prior entry survives) and ``sync_durable_fts`` returns ``False``
    so the create choke point can record the gap (and the brain-pass backstop
    re-indexes on its next tick).
    """
    from agent_memory_lite.fts.durable_fts import sync_durable_fts  # noqa: PLC0415

    c = sqlite3.connect(":memory:", factory=_FailDurableInsertConn, isolation_level=None)
    assert isinstance(c, _FailDurableInsertConn)
    c.row_factory = sqlite3.Row
    apply_migrations(c)
    try:
        did = _decision(c, "kelly atomic case", "quarter kelly sizing")
        # Baseline: the row is indexed.
        assert did in _ids(
            search_kind_fts(c, workspace_id="ws", kind="decision", query="kelly", limit=5)
        )
        # Force the re-index INSERT to fail mid-sync.
        c.fail_durable_insert = True
        ok = sync_durable_fts(c, kind="decision", object_id=did, workspace_id="ws")
        c.fail_durable_insert = False
        # 1) The failure is surfaced, not silently swallowed.
        assert ok is False
        # 2) The DELETE was rolled back by the savepoint -> the row is STILL
        #    findable. Pre-fix this assertion failed (the row was de-indexed).
        assert did in _ids(
            search_kind_fts(c, workspace_id="ws", kind="decision", query="kelly", limit=5)
        )
    finally:
        c.close()


def test_brain_pass_rebuild_step_reindexes(conn: sqlite3.Connection) -> None:
    """The eventual-consistency backstop: a stale/empty durable_fts is rebuilt."""
    from agent_memory_lite.config.settings import Settings  # noqa: PLC0415
    from agent_memory_lite.maintenance.brain_pass import (  # noqa: PLC0415
        BrainPassReport,
        _step_durable_fts_rebuild,
    )
    from agent_memory_lite.utils.time import iso_now  # noqa: PLC0415

    did = _decision(conn, "kelly hygiene case", "quarter kelly sizing")
    conn.execute("DELETE FROM durable_fts")  # simulate a bypass-stale index
    conn.commit()
    assert not search_kind_fts(conn, workspace_id="ws", kind="decision", query="kelly", limit=5)
    report = BrainPassReport(workspace_id="ws", started_at=iso_now(), finished_at="")
    _step_durable_fts_rebuild(conn, "ws", iso_now(), Settings(), report)
    conn.commit()
    assert report.durable_fts_reindexed >= 1
    assert did in _ids(
        search_kind_fts(conn, workspace_id="ws", kind="decision", query="kelly", limit=5)
    )
